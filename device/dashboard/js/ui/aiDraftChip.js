// "✦ AI draft" chip (dashed berry border; see css/base.css .chip--ai). AI output carries this label until a
// therapist approves it (spec "Security").
import { h } from "../core/dom.js";

export function aiDraftChip(text = "AI draft") {
  return h("span", { class: "chip chip--ai" }, text);
}
