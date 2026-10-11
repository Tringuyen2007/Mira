"""Retell emergency calls (§8.4, §8.6.4): escalate, watch the call by polling, push live updates.

With CALLS_DISABLED=1 (the default) or without Retell credentials, the notifier runs a log-only call
simulator: it logs the exact dynamic variables it would send and walks registered → ongoing → ended
on the same code path, so the whole incident flow is testable without placing a call. Only the
three SDK calls (create_phone_call, retrieve, update_live) differ between the two modes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from server.agents.incident_snapshot import call_variables, time_local
from server.config import cfg
from server.logging_setup import get_logger, mask_phone, scrub
from server.notify.policy import CallLedger
from server.notify.templates import render_opening

if TYPE_CHECKING:
    from server.agents.safety_guardian import Incident, SafetyGuardian

log = get_logger("retell")

CONTACT = "emergency_contact"
THERAPIST = "therapist"
TEST = "test"
RETRY_DELAYS_S = (0.0, 2.0, 5.0)
POLL_INTERVAL_S = 3.0
MAX_POLLS = 80  # ~4 min at 3 s
NOT_CONNECTED_RETRY_S = 60.0
FINAL_STATUSES = ("ended", "not_connected", "error")
NO_ANSWER_REASONS = ("voicemail_reached", "dial_no_answer", "dial_busy", "dial_failed")
# The voice agent's model or Retell itself failed (not the person): call again at once with the backup agent.
AGENT_FAILURE_REASONS = ("error_llm_websocket_open", "error_llm_websocket_lost_connection",
                         "error_llm_websocket_runtime", "error_llm_websocket_corrupt_payload", "error_retell",
                         "error_unknown")
BACKUP_SUFFIX = ":backup"
SIM_RING_S = 2.0
# Per-request limits: the SDK default (60 s read, 2 internal retries) could hold an escalation for minutes.
CREATE_TIMEOUT_S = 10.0
RETRIEVE_TIMEOUT_S = 5.0
UPDATE_TIMEOUT_S = 5.0
SIM_ANALYSIS_DELAY_S = 1.0


@dataclass(frozen=True)
class CallView:
    """The parts of a Retell call we act on. `analysis` is None until post-call analysis exists."""

    status: str
    disconnection_reason: str = ""
    analysis: dict[str, Any] | None = None
    in_voicemail: bool = False


@dataclass
class SimulatedCall:
    started: float
    duration_s: float
    ring_s: float = SIM_RING_S

    def view(self, now: float) -> CallView:
        elapsed = now - self.started
        if elapsed < self.ring_s:
            return CallView("registered")
        if elapsed < self.ring_s + self.duration_s:
            return CallView("ongoing")
        done = elapsed >= self.ring_s + self.duration_s + min(SIM_ANALYSIS_DELAY_S, self.ring_s)
        notes = "Simulated call (CALLS_DISABLED=1): nobody was called."
        return CallView("ended", "simulated", {"notes": notes} if done else None)


class RetellNotifier:
    def __init__(self, *, client: Any | None = None, sim_duration_s: float | None = None, sim_ring_s: float = SIM_RING_S,
                 poll_interval_s: float = POLL_INTERVAL_S, max_polls: int = MAX_POLLS,
                 retry_delays_s: tuple[float, ...] = RETRY_DELAYS_S,
                 not_connected_retry_s: float = NOT_CONNECTED_RETRY_S) -> None:
        configured = bool(cfg.RETELL_API_KEY and cfg.RETELL_FROM_NUMBER and cfg.RETELL_AGENT_ID)
        self.mode = "live" if (not cfg.CALLS_DISABLED and (configured or client is not None)) else "log_only"
        if not cfg.CALLS_DISABLED and self.mode == "log_only":
            log.error("CALLS_DISABLED=0 but RETELL_API_KEY / RETELL_FROM_NUMBER / RETELL_AGENT_ID are not all set;"
                      " falling back to log-only calls")
        self._client_obj = client
        self.sim_duration_s = sim_duration_s if sim_duration_s is not None else cfg.SIM_CALL_DURATION_S
        self.sim_ring_s = sim_ring_s
        self.poll_interval_s = poll_interval_s
        self.max_polls = max_polls
        self.retry_delays_s = retry_delays_s
        self.not_connected_retry_s = not_connected_retry_s
        self.ledger = CallLedger()
        self.guardian: SafetyGuardian | None = None
        self._escalations: dict[str, dict[str, Any]] = {}  # alert_id → result (one escalation per alert)
        self._by_call: dict[str, Incident] = {}
        self._sims: dict[str, SimulatedCall] = {}
        self._watchers: set[asyncio.Task[None]] = set()
        self._updates_pushed = 0

    def attach(self, guardian: SafetyGuardian) -> None:
        self.guardian = guardian

    # ---- escalation ------------------------------------------------------------------------------

    async def escalate(self, inc: Incident) -> dict[str, Any]:
        """Call the emergency contact. Never raises; returns call ids / errors for alerts.payload."""
        if inc.alert_id in self._escalations:
            return self._escalations[inc.alert_id]
        out: dict[str, Any] = {"mode": self.mode}
        self._escalations[inc.alert_id] = out
        try:
            p = inc.profile
            if not p.contact_phone:
                out["error"] = "no_contact_phone"
                log.error("no emergency contact phone; dashboard only", extra={"alert_id": inc.alert_id})
                await self.maybe_call_therapist(inc)
                return out
            variables = call_variables(inc, p.contact_name)
            out["dynamic_variables"] = scrub(variables, [])
            call_id = await self._place_call(inc, CONTACT, p.contact_phone, variables, out)
            if call_id is None:
                await self._log_step(inc, "call_failed", {k: v for k, v in out.items() if k.startswith("error")})
                await self.maybe_call_therapist(inc)
                return out
            out["contact_call_id"] = inc.call_id = call_id
            self._start_watch(inc, call_id)
        except Exception as exc:  # noqa: BLE001 - escalation must never raise into the Guardian
            out["error"] = repr(exc)[:300]
            log.exception("escalation failed", extra={"alert_id": inc.alert_id})
            if "contact_call_id" not in out:
                await self._log_step(inc, "call_failed", {"error": out["error"]})
        return out

    async def _place_call(self, inc: Incident, role: str, to_number: str, variables: dict[str, str],
                          out: dict[str, Any], suffix: str = "", backup: bool = False) -> str | None:
        """Place one call (with retries in live mode). Records it in the per-person ledger. If the main agent's
        call can't be created, the backup agent (another model provider) is tried once, still as the same call."""
        retry = bool(suffix)
        if not self.ledger.may_call(inc.alert_id, role, retry=retry):
            log.warning("call refused by policy", extra={"alert_id": inc.alert_id, "role": role})
            return None
        self.ledger.record(inc.alert_id, role)
        try:
            rendered = render_opening(variables)
        except ValueError as exc:
            out["render_error"] = str(exc)
            log.error("call text has an unrendered variable", extra={"alert_id": inc.alert_id, "role": role})
            rendered = {}
        extra = {"alert_id": inc.alert_id, "role": role, "to": mask_phone(to_number),
                 "agent": "backup" if backup else "main"}
        if self.mode == "log_only":
            call_id = f"sim_{inc.alert_id}" + ("" if role == CONTACT else f"_{role}") + suffix.replace(":", "_")
            self._sims[call_id] = SimulatedCall(asyncio.get_running_loop().time(), self.sim_duration_s,
                                                  self.sim_ring_s)
            log.warning("CALLS_DISABLED: simulating Retell call (no real call placed)", extra={
                **extra, "call_id": call_id, "retell_llm_dynamic_variables": variables,
                "opening": rendered.get("backup_opening" if backup else "opening"),
                "voicemail": rendered.get("voicemail")})
        else:
            call_id = None
            for attempt, delay in enumerate(self.retry_delays_s):
                if delay:
                    await asyncio.sleep(delay)
                try:
                    call_id = await asyncio.to_thread(self._create_call, inc.alert_id, role, to_number,
                                                      variables, suffix, backup)
                    break
                except Exception as exc:  # noqa: BLE001 - recorded in the payload, then retried
                    out[f"error_{role}_{attempt}"] = repr(exc)[:300]
                    log.warning("create_phone_call failed", extra={**extra, "attempt": attempt, "error": repr(exc)})
            if call_id is None and not backup and self.has_backup:
                log.warning("main agent call failed, trying the backup agent", extra=extra)
                extra["agent"] = "backup"
                try:
                    call_id = await asyncio.to_thread(self._create_call, inc.alert_id, role, to_number,
                                                      variables, suffix + BACKUP_SUFFIX, True)
                except Exception as exc:  # noqa: BLE001 - recorded; the caller falls back further
                    out[f"error_{role}_backup"] = repr(exc)[:300]
                    log.warning("create_phone_call failed", extra={**extra, "error": repr(exc)})
            if call_id is None:
                return None
            log.warning("Retell call placed", extra={**extra, "call_id": call_id})
        self._by_call[call_id] = inc
        return call_id

    # ---- test calls (POST /me/test-alert) -----------------------------------------------------------

    async def test_call(self, to_number: str, *, patient_first_name: str | None, contact_name: str | None) -> str | None:
        """Place one short test call ("this is a test, no action needed") to `to_number`. Same create path and
        agent as a real escalation, so it also verifies the number, credentials and caller ID. At most one per
        contact per hour (the ledger keys on the hour bucket, and Retell dedupes on the same idempotency key).
        No watcher, no live updates. Returns the call id, or None when refused or Retell rejected it."""
        name = patient_first_name or "the patient"
        alert_id = f"test_{int(time.time() // 3600)}_{''.join(ch for ch in to_number if ch.isdigit())[-10:]}"
        if not self.ledger.may_call(alert_id, TEST):
            log.warning("test call refused: already placed this hour", extra={"to": mask_phone(to_number)})
            return None
        self.ledger.record(alert_id, TEST)
        situation = (f"This is only a test of Mira's alert system for {name}. {name} is fine and no action is "
                     "needed. You can hang up now.")
        variables = {
            "contact_name": contact_name or "there",
            "patient_first_name": name,
            "patient_phone": f"the number you have for {name}",
            "situation": situation,
            "situation_short": f"This is only a test of Mira's alert system for {name}. No action is needed.",
            "current_status": "This is a test. Nothing has happened.",
            "context": "This call was started from the Mira app's test-alert button.",
            "limits": "This is a test call; Mira is not reporting a real event.",
            "therapist_name": "the care team",
            "therapist_phone": "not available",
        }
        extra = {"alert_id": alert_id, "role": TEST, "to": mask_phone(to_number)}
        if self.mode == "log_only":
            call_id = f"sim_{alert_id}"
            log.warning("CALLS_DISABLED: simulating Retell test call (no real call placed)", extra={
                **extra, "call_id": call_id, "retell_llm_dynamic_variables": variables})
            return call_id
        for attempt, delay in enumerate(self.retry_delays_s):
            if delay:
                await asyncio.sleep(delay)
            try:
                call_id = await asyncio.to_thread(self._create_call, alert_id, TEST, to_number, variables, "")
            except Exception as exc:  # noqa: BLE001 - a test call must never raise into the API
                log.warning("test call create_phone_call failed", extra={**extra, "attempt": attempt,
                                                                         "error": repr(exc)})
                continue
            log.warning("Retell test call placed", extra={**extra, "call_id": call_id})
            return call_id
        return None

    def _client(self) -> Any:
        if self._client_obj is None:
            from retell import Retell

            self._client_obj = Retell(api_key=cfg.RETELL_API_KEY)
        return self._client_obj

    @property
    def has_backup(self) -> bool:
        return bool(cfg.RETELL_BACKUP_AGENT_ID)

    def _create_call(self, alert_id: str, role: str, to_number: str, variables: dict[str, str], suffix: str,
                     backup: bool = False) -> str:
        # The published version retell_setup bound to the number; without it Retell picks the agent's default.
        agent_id, agent_version = ((cfg.RETELL_BACKUP_AGENT_ID, cfg.RETELL_BACKUP_AGENT_VERSION) if backup
                                   else (cfg.RETELL_AGENT_ID, cfg.RETELL_AGENT_VERSION))
        version = {"override_agent_version": agent_version} if agent_version is not None else {}
        call = self._client().call.create_phone_call(
            from_number=cfg.RETELL_FROM_NUMBER,
            to_number=to_number,
            override_agent_id=agent_id,
            **version,
            retell_llm_dynamic_variables=variables,
            metadata={"alert_id": alert_id, "role": role, "agent": "backup" if backup else "main"},
            # A retry can never place a second call. Retell allows only [A-Za-z0-9_-] here.
            idempotency_key=f"{alert_id}_{role}{suffix}".replace(":", "_"),
            timeout=CREATE_TIMEOUT_S,
        )
        return str(call.call_id)

    async def maybe_call_therapist(self, inc: Incident) -> None:
        """P1: one call to the therapist when the contact can't be reached; else dashboard only."""
        p = inc.profile
        if not p.therapist_phone or not self.ledger.may_call(inc.alert_id, THERAPIST):
            log.info("therapist call skipped (no phone or already called); dashboard only",
                     extra={"alert_id": inc.alert_id})
            return
        out: dict[str, Any] = {"role": THERAPIST}
        try:
            call_id = await self._place_call(inc, THERAPIST, p.therapist_phone, call_variables(inc, p.therapist_name),
                                             out)
        except Exception as exc:  # noqa: BLE001
            call_id, out["error"] = None, repr(exc)[:300]
        out["call_id"] = call_id
        await self._log_step(inc, "therapist_call", out)

    # ---- watching the call -----------------------------------------------------------------------

    def _start_watch(self, inc: Incident, call_id: str) -> None:
        task = asyncio.create_task(self.watch_call(inc, call_id), name=f"retell-watch-{call_id}")
        self._watchers.add(task)
        task.add_done_callback(self._watchers.discard)

    async def watch_call(self, inc: Incident, call_id: str) -> None:
        """Poll every 3 s (max ~4 min): log steps, read post-call analysis, then close the incident."""
        seen: set[str] = set()
        done = cancelled = False
        try:
            for _ in range(self.max_polls):
                view = await self._retrieve(call_id)
                if view is not None:
                    inc.call_id, inc.call_state = call_id, view.status
                    if view.status not in seen:
                        seen.add(view.status)
                        await self._log_step(inc, f"call_{view.status}", {
                            "call_id": call_id, "disconnection_reason": view.disconnection_reason or None})
                        if view.status == "ongoing" and self.guardian is not None:
                            self.guardian.flush_updates(inc)  # updates queued while it was ringing
                    if view.status in FINAL_STATUSES and (view.analysis is not None or view.status != "ended"):
                        next_call = await self._after_call(inc, call_id, view)
                        if next_call is None:
                            done = True
                            return
                        call_id, seen = next_call, set()
                        continue
                await asyncio.sleep(self.poll_interval_s)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception:  # noqa: BLE001 - logged, then the incident is still closed below
            log.exception("call watcher failed", extra={"alert_id": inc.alert_id, "call_id": call_id})
        finally:
            if not cancelled:
                try:
                    if not done:  # analysis never arrived: still record what we know
                        await self._log_step(inc, "call_watch_timeout", {"call_id": call_id, "seen": sorted(seen)})
                finally:
                    if self.guardian is not None:
                        await self.guardian.close(inc)

    async def _after_call(self, inc: Incident, call_id: str, view: CallView) -> str | None:
        """Acknowledge; call again with the backup agent if the voice model failed; retry once if not connected;
        or fall back to the therapist. Returns a new call id to watch."""
        data = view.analysis or {}
        if data.get("will_check_on_patient"):
            await self._log_step(inc, "acknowledged", {"call_id": call_id, "notes": data.get("notes"),
                                                      "contact_reached": data.get("contact_reached")})
            return None
        if (view.disconnection_reason in AGENT_FAILURE_REASONS and self.has_backup
                and self.ledger.may_call(inc.alert_id, CONTACT, retry=True)):
            await self._log_step(inc, "call_backup", {"call_id": call_id, "reason": view.disconnection_reason})
            backup_out: dict[str, Any] = {}
            new_id = await self._place_call(inc, CONTACT, inc.profile.contact_phone or "",
                                            call_variables(inc, inc.profile.contact_name), backup_out,
                                            suffix=BACKUP_SUFFIX, backup=True)
            if new_id is not None:
                return new_id
        if view.status != "ended" and self.ledger.may_call(inc.alert_id, CONTACT, retry=True):
            await self._log_step(inc, "call_retry", {"call_id": call_id, "after_s": self.not_connected_retry_s})
            await asyncio.sleep(self.not_connected_retry_s)
            out: dict[str, Any] = {}
            new_id = await self._place_call(inc, CONTACT, inc.profile.contact_phone or "",
                                            call_variables(inc, inc.profile.contact_name), out, suffix=":retry")
            if new_id is not None:
                return new_id
        if (view.status != "ended" or view.disconnection_reason in NO_ANSWER_REASONS + AGENT_FAILURE_REASONS
                or view.in_voicemail):
            await self.maybe_call_therapist(inc)
        return None

    async def _retrieve(self, call_id: str) -> CallView | None:
        sim = self._sims.get(call_id)
        if sim is not None:
            return sim.view(asyncio.get_running_loop().time())
        try:
            call = await asyncio.to_thread(self._client().call.retrieve, call_id, timeout=RETRIEVE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - keep polling; the watcher has its own time limit
            log.warning("call.retrieve failed", extra={"call_id": call_id, "error": repr(exc)})
            return None
        analysis = getattr(call, "call_analysis", None)
        data = getattr(analysis, "custom_analysis_data", None) if analysis is not None else None
        return CallView(
            status=str(call.call_status),
            disconnection_reason=str(getattr(call, "disconnection_reason", "") or ""),
            analysis=(dict(data) if isinstance(data, dict) else {}) if analysis is not None else None,
            in_voicemail=bool(getattr(analysis, "in_voicemail", False)),
        )

    # ---- live updates ----------------------------------------------------------------------------

    async def push_update(self, inc: Incident, text: str, status: str) -> bool:
        """Add an UPDATE to the ongoing call. Throttling/dedupe is the Guardian's job. True if delivered."""
        if not inc.call_id or inc.call_state != "ongoing":
            return False
        call_control = {"additional_context": f"UPDATE {time_local(time.time())}: {text}", "trigger_response": True}
        fields_to_override = {"override_dynamic_variables": {"current_status": status}}
        if inc.call_id in self._sims:
            log.warning("CALLS_DISABLED: simulating live call update", extra={
                "alert_id": inc.alert_id, "call_id": inc.call_id, "call_control": call_control,
                "fields_to_override": fields_to_override})
        else:
            try:
                await asyncio.to_thread(self._client().call.update_live, inc.call_id, call_control=call_control,
                                        fields_to_override=fields_to_override, timeout=UPDATE_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - e.g. the call just ended; the facts stay in alerts
                log.warning("update_live failed", extra={"alert_id": inc.alert_id, "call_id": inc.call_id,
                                                         "error": repr(exc)})
                return False
        self._updates_pushed += 1
        await self._log_step(inc, "call_update", {"call_id": inc.call_id, "text": call_control["additional_context"],
                                                  "current_status": status})
        return True

    # ---- webhooks, stats, shutdown ---------------------------------------------------------------

    async def on_webhook(self, event: str, call: dict[str, Any]) -> None:
        """Record a verified Retell webhook on the matching incident. Polling stays the source of truth."""
        call_id = str(call.get("call_id") or "")
        inc = self._by_call.get(call_id)
        log.info("retell webhook event", extra={"event": event, "call_id": call_id or None,
                                                "alert_id": inc.alert_id if inc else None})
        if inc is not None:
            await self._log_step(inc, f"webhook_{event}", {"call_id": call_id,
                                                          "call_status": call.get("call_status")})

    async def _log_step(self, inc: Incident, step: str, payload: dict[str, Any]) -> None:
        if self.guardian is not None:
            await self.guardian.log(inc, step, payload)
        else:
            log.info(f"alert {step}", extra={"alert_id": inc.alert_id, "payload": payload})

    def stats(self) -> dict[str, Any]:
        return {"mode": self.mode, "escalations": len(self._escalations), "watching": len(self._watchers),
                "updates_pushed": self._updates_pushed}

    async def stop(self) -> None:
        for task in list(self._watchers):
            task.cancel()
        await asyncio.gather(*self._watchers, return_exceptions=True)
