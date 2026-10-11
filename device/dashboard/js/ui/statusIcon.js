// Status icon + visually-hidden label (status is never colour-alone; spec "Visual system (hi-fi)").
import { h } from "../core/dom.js";
import { icon } from "./icons.js";

const ICON_FOR = { needs: "alert-triangle", watch: "diamond", ok: "check-circle" };
const LABEL_FOR = { needs: "Needs you", watch: "Watch", ok: "On track" };

export function statusIcon(kind) {
  return h("span", { class: `status-icon status-icon--${kind}` },
    icon(ICON_FOR[kind] || "diamond"),
    h("span", { class: "sr-only" }, LABEL_FOR[kind] || ""));
}
