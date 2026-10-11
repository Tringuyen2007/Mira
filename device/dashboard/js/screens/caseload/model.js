// Pure view-model for the Caseload screen: caseloadModel(caseloadJson, proposalsJson, opts) -> plain data the
// view renders with h(). No DOM here so it can be unit-tested without fakedom.
import { href } from "../../core/router.js";
import { fmtWhen, fmtDateLong, firstName } from "../../core/format.js";
import { proposalSummary } from "../../ui/proposalCard.js";

const ON_TRACK_SHOWN = 1;

const TABS = [
  { key: "all", label: "All", bucket: null },
  { key: "needs", label: "Needs you", bucket: "needs_you" },
  { key: "watch", label: "Watch", bucket: "watch" },
  { key: "on_track", label: "On track", bucket: "on_track" },
];

const GROUPS = [
  { key: "needs_you", label: "Needs you" },
  { key: "watch", label: "Watch" },
  { key: "on_track", label: "On track" },
];

const STATUS_FOR_BUCKET = { needs_you: "needs", watch: "watch", on_track: "ok" };

function capitalize(s) {
  const str = String(s || "");
  return str ? str[0].toUpperCase() + str.slice(1) : str;
}

function toRow(p, now) {
  const sub = [capitalize(p.condition), p.week != null ? `wk ${p.week}` : null].filter(Boolean).join(" · ");
  return {
    id: p.id,
    name: p.name,
    age: p.age,
    sub,
    statusIcon: STATUS_FOR_BUCKET[p.bucket] || "ok",
    reason: p.reason,
    when: p.live_now ? "Live now" : `Last session ${fmtWhen(p.last_session_at, now)}`,
    evidence: p.evidence,
    evidenceTone: !p.evidence ? "empty" : p.bucket === "needs_you" ? "alert" : "plain",
    href: href("patient", { pid: p.id }),
  };
}

function buildLive(today) {
  const live = today && today.live_now;
  if (!live) return null;
  return {
    name: live.name,
    line: `${capitalize(live.exercise_name)} · set ${live.set} of ${live.sets_target} · rep ${live.rep}`,
    href: href("live", { pid: live.patient_id }),
  };
}

function buildCopilot(proposalsJson) {
  if (!proposalsJson || !Array.isArray(proposalsJson.proposals)) return null;
  const proposals = proposalsJson.proposals;
  const items = proposals.slice(0, 3).map((p) => ({
    text: `${firstName(p.patient_name)}: ${proposalSummary(p)}`,
  }));
  return { count: proposals.length, items };
}

export function caseloadModel(caseloadJson, proposalsJson, { filter = "all", search = "", showAllOnTrack = false,
                                                              now = new Date() } = {}) {
  // Minor 14: an unknown `?filter=` used to leave no tab active; fall back to "all" like the default.
  const activeKey = TABS.some((t) => t.key === filter) ? filter : "all";
  const counts = (caseloadJson && caseloadJson.counts) || { all: 0, needs_you: 0, watch: 0, on_track: 0 };
  const tabs = TABS.map((t) => ({
    key: t.key, label: t.label, count: t.key === "all" ? counts.all : counts[t.bucket],
    active: activeKey === t.key,
  }));

  const activeTab = TABS.find((t) => t.key === activeKey) || TABS[0];
  const needle = search.trim().toLowerCase();
  const allPatients = (caseloadJson && caseloadJson.patients) || [];

  const bucketsToShow = activeTab.bucket ? GROUPS.filter((g) => g.key === activeTab.bucket) : GROUPS;

  let moreOnTrack = 0;
  const groups = bucketsToShow.map((g) => {
    let rows = allPatients.filter((p) => p.bucket === g.key);
    if (needle) rows = rows.filter((p) => String(p.name || "").toLowerCase().includes(needle));
    const count = rows.length;
    if (g.key === "on_track" && !needle && !showAllOnTrack && rows.length > ON_TRACK_SHOWN) {
      moreOnTrack = rows.length - ON_TRACK_SHOWN;
      rows = rows.slice(0, ON_TRACK_SHOWN);
    }
    return { key: g.key, label: g.label, count, rows: rows.map((p) => toRow(p, now)) };
  });

  return {
    dateLabel: fmtDateLong(now, now),
    subtitle: `${counts.all} patients, sorted by who needs you first`,
    tabs,
    groups,
    moreOnTrack,
    today: {
      done: (caseloadJson && caseloadJson.today && caseloadJson.today.sessions_done) || 0,
      live: caseloadJson && caseloadJson.today && caseloadJson.today.live_now ? 1 : 0,
    },
    copilot: buildCopilot(proposalsJson),
    live: buildLive(caseloadJson && caseloadJson.today),
  };
}
