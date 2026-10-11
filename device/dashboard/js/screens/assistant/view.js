// Mira Assistant view builders (Figma 209:490, renamed from Care Copilot). Every builder returns fresh elements;
// index.js owns which persistent region each one goes into, so the composer's <textarea> is built once per mount
// and never re-rendered while the therapist types. All AI text goes in as text nodes through h().
import { h } from "../../core/dom.js";
import { aiDraftChip } from "../../ui/aiDraftChip.js";
import { lineChart } from "../../ui/lineChart.js";

export const SUGGESTIONS = [
  "Summarise today",
  "Who missed sessions?",
  "Which of my patients are compensating?",
  "Draft progress notes",
];
export const MAX_QUESTION = 500;

export function renderHead({ onPatientChange, onReview }) {
  const eyebrowEl = h("p", { class: "meta assistant__eyebrow" }, "Across your caseload");
  const select = h("select", { id: "assistant-patient", class: "assistant__picker",
    onchange: (e) => onPatientChange(e.target.value || null) },
  h("option", { value: "" }, "All patients"));
  const reviewBtn = h("button", { class: "btn btn--secondary", type: "button", hidden: true, onclick: onReview },
    "Review this patient");
  const el = h("div", { class: "page-head assistant__head" },
    h("div", {},
      eyebrowEl,
      h("h1", { class: "page-title" }, "Mira Assistant"),
      h("p", { class: "page-sub" },
        "Ask about any patient. Everything Mira Assistant writes stays an AI draft until you approve it.")),
    h("div", { class: "assistant__head-actions" },
      reviewBtn,
      h("label", { class: "sr-only", for: "assistant-patient" }, "Patient"),
      select));
  return { el, eyebrowEl, select, reviewBtn };
}

export function patientOptions(select, patients, selectedId) {
  select.replaceChildren(h("option", { value: "" }, "All patients"),
    ...patients.map((p) => h("option", { value: p.id, selected: p.id === selectedId }, p.name)));
  select.value = selectedId || "";
}

export function renderComposer({ onSend, onSuggestion, onInput }) {
  const counter = h("span", { class: "meta assistant__counter", id: "assistant-counter" }, `0/${MAX_QUESTION}`);
  const textarea = h("textarea", {
    id: "assistant-question", class: "assistant__input", rows: 2, maxlength: MAX_QUESTION,
    placeholder: "Ask Mira Assistant about a patient…", "aria-describedby": "assistant-counter",
    oninput: (e) => onInput(e.target.value),
    onkeydown: (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        onSend();
      }
    },
  });
  const sendBtn = h("button", { class: "btn btn--primary", type: "button", onclick: () => onSend() }, "Send");
  const chips = SUGGESTIONS.map((q) =>
    h("button", { class: "assistant__suggestion", type: "button", onclick: () => onSuggestion(q) }, q));
  const thinkingEl = h("p", { class: "assistant__thinking meta", role: "status", "aria-live": "polite" });
  const el = h("div", { class: "assistant__composer" },
    thinkingEl,
    h("div", { class: "assistant__suggestions", role: "group", "aria-label": "Suggested questions" }, chips),
    h("div", { class: "assistant__input-row" },
      h("label", { class: "sr-only", for: "assistant-question" }, "Question for Mira Assistant"),
      textarea,
      sendBtn),
    counter);
  return { el, textarea, sendBtn, chips, counter, thinkingEl };
}

export function renderIntro() {
  return h("div", { class: "assistant__intro" },
    h("p", { class: "assistant__intro-title" }, "Ask Mira Assistant"),
    h("p", { class: "meta" },
      "Ask about one patient or your whole caseload. Mira Assistant looks at the data, explains what changed and " +
      "drafts notes, messages and plan changes for you to review."));
}

export function renderQuestion(text) {
  return h("div", { class: "bubble bubble--therapist" }, h("p", {}, text));
}

function blockEl(b) {
  if (b.type === "p") return h("p", {}, b.text);
  return h(b.type, { class: `answer__list answer__list--${b.type}` }, b.items.map((t) => h("li", {}, t)));
}

function assistantLabel(extra) {
  return h("div", { class: "bubble__label" },
    h("span", { class: "bubble__who" }, "Mira Assistant"), aiDraftChip(), extra || null);
}

// model: answerModel(); opts: {draftName(pid), canSend, note: {show, disabledReason}, onAddNote, onCopy,
// onSendDraft, onShowProposal}.
export function renderAnswer(model, opts) {
  const charts = model.charts.map((c) =>
    h("figure", { class: "answer__chart" },
      c.title ? h("figcaption", { class: "answer__chart-title" }, c.title) : null,
      lineChart(c)));
  const evidence = model.evidence.length
    ? h("ul", { class: "answer__evidence", "aria-label": "Evidence" },
      model.evidence.map((t) => h("li", { class: "chip answer__chip" }, t)))
    : null;
  const proposals = model.proposalsNote
    ? h("p", { class: "answer__proposals" }, model.proposalsNote, " ",
      h("button", { class: "link-button", type: "button", onclick: () => opts.onShowProposal?.(model.proposalIds[0]) },
        "Open proposal"))
    : null;
  const drafts = model.drafts.map((d) => renderDraft(d, opts));
  const sql = model.sql.length
    ? h("details", { class: "answer__sql" },
      h("summary", {}, "Show the query"),
      model.sql.map((q) => h("pre", {}, h("code", {}, q))))
    : null;

  const noteHost = h("div", { class: "answer__note-host" });
  let noteBtn = null;
  if (opts.note && opts.note.show) {
    noteBtn = h("button", {
      class: "btn btn--secondary", type: "button",
      disabled: Boolean(opts.note.disabledReason), title: opts.note.disabledReason || null,
      onclick: () => opts.onAddNote?.(noteHost, noteBtn),
    }, "Add to progress note");
  }
  const el = h("div", { class: "bubble bubble--assistant" },
    assistantLabel(model.cached ? h("span", { class: "chip" }, "Saved answer") : null),
    model.fallback ? h("p", { class: "answer__fallback" }, model.fallback) : null,
    h("div", { class: "answer__body" }, model.blocks.map(blockEl)),
    charts,
    evidence,
    proposals,
    drafts,
    sql,
    noteBtn ? h("div", { class: "answer__actions" }, noteBtn,
      opts.note.disabledReason ? h("span", { class: "meta" }, opts.note.disabledReason) : null) : null,
    noteHost);
  return { el, noteBtn, noteHost };
}

export function renderDraft(draft, opts) {
  const textEl = h("p", { class: "draft__text" }, draft.text);
  const actions = h("div", { class: "draft__actions" });
  actions.append(h("button", { class: "btn btn--secondary", type: "button",
    onclick: () => opts.onCopy?.(draft, textEl) }, "Copy text"));
  if (opts.canSend) {
    const sendBtn = h("button", { class: "btn btn--primary", type: "button",
      onclick: () => opts.onSendDraft?.(draft, sendBtn, el) }, "Approve and send");
    actions.append(sendBtn);
  }
  const el = h("div", { class: "draft" },
    h("div", { class: "draft__head" },
      h("p", { class: "draft__to" }, `Draft to ${opts.draftName ? opts.draftName(draft.patientId) : draft.patientId}`),
      h("span", { class: "draft__chip" }, aiDraftChip())),
    textEl,
    actions);
  return el;
}

export function renderError(message, onRetry) {
  return h("div", { class: "bubble bubble--assistant bubble--error", role: "alert" },
    h("div", { class: "bubble__label" }, h("span", { class: "bubble__who" }, "Mira Assistant")),
    h("p", {}, message),
    onRetry ? h("button", { class: "btn btn--secondary", type: "button", onclick: onRetry }, "Try again") : null);
}

// Inline progress-note editor: prefilled with the answer, saved only by the explicit approve click.
export function renderNoteEditor({ patientName, text, onApprove, onCancel }) {
  const id = `note-${Math.random().toString(36).slice(2, 9)}`;
  const textarea = h("textarea", { id, class: "note-editor__text", rows: 6, maxlength: 4000 });
  textarea.value = text;
  const errorEl = h("p", { class: "note-editor__error", role: "alert" });
  const approveBtn = h("button", { class: "btn btn--primary", type: "button",
    onclick: () => onApprove(textarea.value, { approveBtn, cancelBtn, errorEl }) }, "Approve as progress note");
  const cancelBtn = h("button", { class: "btn btn--ghost", type: "button", onclick: onCancel }, "Cancel");
  const el = h("div", { class: "note-editor" },
    h("div", { class: "note-editor__head" },
      h("label", { class: "note-editor__label", for: id }, `Progress note for ${patientName}`), aiDraftChip()),
    h("p", { class: "meta" }, "Saved to the latest session once you approve it. Edit anything first."),
    textarea,
    errorEl,
    h("div", { class: "note-editor__actions" }, cancelBtn, approveBtn));
  return { el, textarea };
}

export function renderNoteApproved(patientName) {
  return h("p", { class: "note-editor__done" }, `Approved as a progress note for ${patientName}.`);
}

export function renderRailTabs(tabsModel, onTab) {
  const tabs = createRailTabs(onTab);
  tabs.update(tabsModel);
  return tabs.el;
}

// Built once per mount, updated in place (aria-selected, pending count) so the focused tab survives every rail
// refresh (final review B, I4).
export function createRailTabs(onTab) {
  const byKey = new Map();
  const el = h("div", { class: "pill-tabs assistant__tabs", role: "tablist", "aria-label": "Proposals by status" });
  function update(tabsModel) {
    for (const t of tabsModel.tabs) {
      let ref = byKey.get(t.key);
      if (!ref) {
        const countEl = h("span", { class: "pill-tabs__count" });
        const btn = h("button", { type: "button", role: "tab", "data-tab": t.key, onclick: () => onTab(t.key) },
          t.label);
        ref = { btn, countEl };
        byKey.set(t.key, ref);
        el.append(btn);
      }
      ref.btn.setAttribute("aria-selected", t.active ? "true" : "false");
      const showCount = t.key === "pending" && t.count > 0;
      ref.countEl.textContent = showCount ? ` ${t.count}` : "";
      if (showCount && ref.countEl.parentNode !== ref.btn) ref.btn.append(ref.countEl);
      else if (!showCount) ref.countEl.remove();
    }
  }
  return { el, update };
}

const EMPTY_TAB = {
  pending: "No proposals waiting. Mira Assistant drafts plan changes here for you to review.",
  approved: "Nothing approved yet.",
  dismissed: "Nothing dismissed yet.",
};

export function renderRailEmpty(tab) {
  return h("p", { class: "meta assistant__rail-empty" }, EMPTY_TAB[tab] || "");
}
