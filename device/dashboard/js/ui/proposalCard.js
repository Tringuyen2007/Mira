import { h } from "../core/dom.js";
import { aiDraftChip } from "./aiDraftChip.js";
import { statusIcon } from "./statusIcon.js";

// Proposal copy. proposalSummary() turns a proposal's `change` into a short imperative phrase for the
// Caseload's Mira Assistant preview; proposalCard() is the full card (approve/edit/dismiss) used by the Mira
// Assistant proposals rail.

const EXERCISE_PLURAL = {
  squat: "squats", squats: "squats", lunge: "lunges", lunges: "lunges", bridge: "bridges", bridges: "bridges",
};

// Known add-on drill ids -> their display name (hyphenated compound, Figma copy). An id with no entry falls
// back to a plain space-separated reading, which won't always be grammatically ideal but never throws.
const DRILL_NAMES = {
  weight_shift_drill: "weight-shift drill",
};

function pluralExercise(exerciseId) {
  if (!exerciseId) return "the exercise";
  return EXERCISE_PLURAL[exerciseId] || `${exerciseId}s`;
}

function firstSentence(text) {
  const s = String(text || "").trim();
  if (!s) return "";
  const m = s.match(/^.*?[.!?](?=\s|$)/);
  return m ? m[0] : s;
}

export function proposalSummary(proposal) {
  const change = (proposal && proposal.change) || {};
  if (Array.isArray(change.add) && change.add.length > 0) {
    const id = String(change.add[0]);
    const name = DRILL_NAMES[id] || id.replace(/_/g, " ");
    return `add a ${name}`;
  }
  if (change.sets != null && change.reps != null) {
    return `change ${pluralExercise(change.exercise_id)} to ${change.sets} × ${change.reps}`;
  }
  if (change.targets && change.targets.knee_deg != null) {
    return `raise knee bend target to ${Math.round(change.targets.knee_deg)}°`;
  }
  return firstSentence(proposal && proposal.rationale);
}

// ---- The proposal card (Mira Assistant proposals rail) ------------------------------------------------------

const DECIDED_LABEL = { approved: "Approved", edited: "Approved with changes", dismissed: "Dismissed" };

const FIELDS = [
  { name: "sets", label: "Sets", min: 1, max: 10 },
  { name: "reps", label: "Reps", min: 1, max: 30 },
  { name: "rest_days_between", label: "Rest days between", min: 0, max: 3 },
  { name: "knee_deg", label: "Knee bend target (°)", min: 0, max: 180 },
];

function capitalize(s) {
  const str = String(s || "");
  return str ? str[0].toUpperCase() + str.slice(1) : str;
}

function drillName(id) {
  return DRILL_NAMES[id] || String(id).replace(/_/g, " ");
}

// One line per field the proposal changes: "Squats: 3 × 12 → 3 × 8" when the active prescription (`current`,
// a row of GET /prescriptions or GET /patients/{id}) is known, otherwise just the new value.
export function changeRows(change, current) {
  const c = change || {};
  const cur = current || null;
  const curTargets = (cur && cur.targets) || {};
  const exercise = capitalize(pluralExercise(c.exercise_id).replace(/_/g, " "));
  const rows = [];
  if (c.sets != null || c.reps != null) {
    const sets = c.sets ?? (cur && cur.sets);
    const reps = c.reps ?? (cur && cur.reps);
    if (cur && cur.sets != null && cur.reps != null) rows.push(`${exercise}: ${cur.sets} × ${cur.reps} → ${sets} × ${reps}`);
    else if (sets != null && reps != null) rows.push(`${exercise}: ${sets} × ${reps}`);
    else if (c.sets != null) rows.push(`${exercise}: ${c.sets} sets`);
    else rows.push(`${exercise}: ${c.reps} reps`);
  }
  if (c.rest_days_between != null) {
    const label = `Rest days between ${String(c.exercise_id || "exercise").replace(/_/g, " ")} days`;
    rows.push(cur ? `${label}: ${curTargets.rest_days_between ?? 0} → ${c.rest_days_between}`
      : `${label}: ${c.rest_days_between}`);
  }
  const knee = c.targets && c.targets.knee_deg;
  if (knee != null) {
    rows.push(curTargets.knee_deg != null
      ? `Knee bend target: ${Math.round(curTargets.knee_deg)}° → ${Math.round(knee)}°`
      : `Knee bend target: ${Math.round(knee)}°`);
  }
  if (Array.isArray(c.add) && c.add.length) rows.push(`Add: ${c.add.map(drillName).join(", ")}`);
  if (Array.isArray(c.remove) && c.remove.length) rows.push(`Remove: ${c.remove.map(drillName).join(", ")}`);
  return rows;
}

// Edit form values (strings) -> {ok, change} (a PlanChange for POST /proposals/{id}/edit) or {ok: false, error}.
// The proposal's add/remove and other targets are kept so editing the numbers never silently drops a drill.
export function editChangeFrom(original, values) {
  const o = original || {};
  if (!o.exercise_id) return { ok: false, error: "This proposal has no exercise to change." };
  const change = { exercise_id: o.exercise_id };
  const parsed = {};
  for (const f of FIELDS) {
    const raw = String(values[f.name] ?? "").trim();
    if (raw === "") continue;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < f.min || n > f.max) {
      return { ok: false, error: `${f.label.replace(/ \(°\)$/, "")} must be a whole number from ${f.min} to ${f.max}.` };
    }
    parsed[f.name] = n;
  }
  if (parsed.sets != null) change.sets = parsed.sets;
  if (parsed.reps != null) change.reps = parsed.reps;
  if (parsed.rest_days_between != null) change.rest_days_between = parsed.rest_days_between;
  const targets = { ...(o.targets || {}) };
  delete targets.knee_deg;
  if (parsed.knee_deg != null) targets.knee_deg = parsed.knee_deg;
  if (Object.keys(targets).length) change.targets = targets;
  if (Array.isArray(o.add) && o.add.length) change.add = [...o.add];
  if (Array.isArray(o.remove) && o.remove.length) change.remove = [...o.remove];
  if (Object.keys(change).length === 1) return { ok: false, error: "Change at least one value." };
  return { ok: true, change };
}

function fmtDecidedAt(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" }).format(d);
}

function setDisabled(buttons, on) {
  for (const b of buttons) {
    if (on) b.setAttribute("disabled", "");
    else b.removeAttribute("disabled");
  }
}

// proposal: a row of GET /proposals. opts: {current, patientStatus ("needs" | "watch" | "ok"), highlighted,
// onApprove(p), onDismiss(p), onEdit(p, change)}. Each handler returns a promise of `true` when the decision went
// through (the card stays disabled while it leaves) or `false` (buttons come back).
export function proposalCard(proposal, opts = {}) {
  const p = proposal || {};
  const pending = p.status === "pending";
  const change = p.change || {};
  const titleId = `proposal-${p.id}-title`;
  const why = firstSentence(p.rationale);
  const items = (p.evidence && Array.isArray(p.evidence.items)) ? p.evidence.items.filter((s) => typeof s === "string") : [];
  const rows = changeRows(change, opts.current);
  const kind = pending ? (opts.patientStatus || "watch") : (p.status === "dismissed" ? "watch" : "ok");

  const actionButtons = [];
  const actionsEl = h("div", { class: "proposal-card__actions" });
  const formHost = h("div", { class: "proposal-card__form-host" });

  async function run(handler, ...args) {
    if (typeof handler !== "function") return;
    setDisabled(actionButtons, true);
    let ok = false;
    try { ok = (await handler(p, ...args)) === true; } catch { ok = false; }
    if (!ok) setDisabled(actionButtons, false);
    return ok;
  }

  function openEdit() {
    const inputs = {};
    const curTargets = (opts.current && opts.current.targets) || {};
    const initial = {
      sets: change.sets ?? (opts.current && opts.current.sets),
      reps: change.reps ?? (opts.current && opts.current.reps),
      rest_days_between: change.rest_days_between ?? curTargets.rest_days_between,
      knee_deg: (change.targets && change.targets.knee_deg) ?? curTargets.knee_deg,
    };
    const errorEl = h("p", { class: "proposal-card__error", role: "alert" });
    const fields = FIELDS.map((f) => {
      const id = `proposal-${p.id}-${f.name}`;
      const input = h("input", { id, name: f.name, type: "number", inputmode: "numeric", min: f.min, max: f.max, step: 1 });
      input.value = initial[f.name] == null ? "" : String(initial[f.name]);
      inputs[f.name] = input;
      return h("div", { class: "proposal-card__field" }, h("label", { for: id }, f.label), input);
    });
    const save = h("button", { class: "btn btn--primary", type: "submit" }, "Approve with changes");
    const cancel = h("button", { class: "btn btn--ghost", type: "button", onclick: closeEdit }, "Cancel");
    const form = h("form", { class: "proposal-card__form", "aria-label": "Edit proposal", novalidate: true },
      h("div", { class: "proposal-card__fields" }, fields), errorEl,
      h("div", { class: "proposal-card__actions" }, cancel, save));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const values = Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value]));
      const res = editChangeFrom(change, values);
      if (!res.ok) { errorEl.textContent = res.error; return; }
      errorEl.textContent = "";
      setDisabled([save, cancel], true);
      let ok = false;
      try { ok = (await opts.onEdit?.(p, res.change)) === true; } catch { ok = false; }
      if (!ok) setDisabled([save, cancel], false);
    });
    formHost.replaceChildren(form);
    actionsEl.hidden = true;
    inputs.sets.focus?.();
  }

  function closeEdit() {
    formHost.replaceChildren();
    actionsEl.hidden = false;
  }

  if (pending) {
    const dismissBtn = h("button", { class: "btn btn--ghost", type: "button", onclick: () => run(opts.onDismiss) }, "Dismiss");
    const editBtn = h("button", { class: "btn btn--secondary", type: "button", onclick: openEdit }, "Edit");
    const approveBtn = h("button", { class: "btn btn--primary", type: "button", onclick: () => run(opts.onApprove) }, "Approve");
    actionButtons.push(dismissBtn, editBtn, approveBtn);
    actionsEl.append(dismissBtn, h("span", { class: "proposal-card__spacer" }), editBtn, approveBtn);
  }

  const decided = pending ? null : h("p", { class: "meta proposal-card__decided" },
    [DECIDED_LABEL[p.status] || capitalize(p.status),
      p.decided_by ? ` by ${p.decided_by}` : "",
      p.decided_at ? ` · ${fmtDecidedAt(p.decided_at)}` : ""].join(""));

  return h("article", {
    class: `card proposal-card${opts.highlighted ? " proposal-card--highlight" : ""}`,
    "data-proposal-id": String(p.id), "aria-labelledby": titleId, tabindex: "-1",
  },
    h("div", { class: "proposal-card__head" },
      h("span", { class: "proposal-card__who" }, statusIcon(kind),
        h("span", { class: "proposal-card__name" }, p.patient_name || p.patient_id || "")),
      pending ? aiDraftChip() : null),
    h("h3", { class: "proposal-card__title", id: titleId }, capitalize(proposalSummary(p))),
    rows.length ? h("ul", { class: "proposal-card__changes" },
      rows.map((r) => h("li", { class: "proposal-card__change" }, r))) : null,
    why ? h("p", { class: "proposal-card__why" }, `Why: ${why}`) : null,
    items.length ? h("details", { class: "proposal-card__evidence" },
      h("summary", {}, `Evidence (${items.length})`),
      h("ul", {}, items.map((t) => h("li", {}, t)))) : null,
    decided,
    pending ? actionsEl : null,
    formHost);
}
