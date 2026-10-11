// Pure view-models for the Mira Assistant screen (no DOM): an /copilot/ask or /copilot/review answer -> blocks,
// charts, chips and drafts; the conversation -> the `context` the server accepts; the proposals rail tabs.

const MAX_CONTEXT_TURNS = 3;
const MAX_CONTEXT_ANSWER = 1200; // server ContextTurn.answer max_length
const MAX_QUESTION = 500; // server AskIn.question / ContextTurn.question max_length

const ORDERED_ITEM = /^\s*\d{1,3}[.)]\s+/;
const BULLET_ITEM = /^\s*[-*•]\s+/;

const str = (v) => (typeof v === "string" ? v : "");
const arr = (v) => (Array.isArray(v) ? v : []);

// Lines become paragraphs; runs of "1." lines become an ordered list, runs of "-" lines an unordered one.
// Blank lines only separate (they also end a list).
function toBlocks(text) {
  const blocks = [];
  let list = null;
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) { list = null; continue; }
    const kind = ORDERED_ITEM.test(line) ? "ol" : BULLET_ITEM.test(line) ? "ul" : null;
    if (kind) {
      const item = line.replace(kind === "ol" ? ORDERED_ITEM : BULLET_ITEM, "");
      if (!list || list.type !== kind) {
        list = { type: kind, items: [] };
        blocks.push(list);
      }
      list.items.push(item);
    } else {
      list = null;
      blocks.push({ type: "p", text: line });
    }
  }
  return blocks;
}

function toChart(c) {
  if (!c || typeof c !== "object") return null;
  const series = arr(c.series).map((s) => ({
    name: str(s && s.name),
    points: arr(s && s.points)
      .filter((p) => Array.isArray(p) && p.length >= 2 && p[1] !== null && Number.isFinite(Number(p[1])))
      .map(([x, y]) => ({ x, y: Number(y) })),
  })).filter((s) => s.points.length > 0);
  if (series.length === 0) return null;
  return { title: str(c.title), xLabel: str(c.x_label), yLabel: str(c.y_label), series };
}

export function fallbackNotice(reason) {
  const r = String(reason).trim().replace(/[.\s]+$/, "");
  return `I couldn't finish this analysis: ${r}. Here is what I found.`;
}

function proposalsNote(n) {
  if (n <= 0) return null;
  return `I drafted ${n} change${n === 1 ? "" : "s"} to the plan. See Proposals.`;
}

export function answerModel(askJson) {
  const j = askJson && typeof askJson === "object" ? askJson : {};
  const text = str(j.answer);
  const proposalIds = arr(j.proposals).filter((id) => Number.isInteger(id));
  return {
    text,
    blocks: toBlocks(text),
    charts: arr(j.charts).map(toChart).filter(Boolean),
    evidence: arr(j.evidence).map(str).filter(Boolean),
    sql: arr(j.sql).map((q) => (typeof q === "string" ? q : str(q && q.query))).filter(Boolean),
    drafts: arr(j.drafts)
      .filter((d) => d && typeof d.patient_id === "string" && typeof d.text === "string" && d.text.trim())
      .map((d) => ({ patientId: d.patient_id, text: d.text })),
    proposalIds,
    proposalsNote: proposalsNote(proposalIds.length),
    fallback: j.fallback ? fallbackNotice(j.fallback) : null,
    cached: j.cached === true,
  };
}

// turns: [{question, status: "pending" | "done" | "error", answerText}] (oldest first). Only answered turns are
// context; the server keeps them as context only (never evidence) — R1a.
export function contextFrom(turns) {
  return arr(turns)
    .filter((t) => t && t.status === "done" && str(t.question).trim())
    .slice(-MAX_CONTEXT_TURNS)
    .map((t) => ({
      question: str(t.question).trim().slice(0, MAX_QUESTION),
      answer: str(t.answerText).slice(0, MAX_CONTEXT_ANSWER),
    }));
}

const TABS = [
  { key: "pending", label: "Waiting", statuses: ["pending"] },
  { key: "approved", label: "Approved", statuses: ["approved", "edited"] },
  { key: "dismissed", label: "Dismissed", statuses: ["dismissed"] },
];

export function proposalsTabsModel(list, tab) {
  const all = arr(list).filter((p) => p && typeof p === "object");
  const active = TABS.find((t) => t.key === tab) || TABS[0];
  const inTab = (t) => all.filter((p) => t.statuses.includes(p.status));
  return {
    tab: active.key,
    tabs: TABS.map((t) => ({ key: t.key, label: t.label, count: inTab(t).length, active: t === active })),
    items: inTab(active),
  };
}
