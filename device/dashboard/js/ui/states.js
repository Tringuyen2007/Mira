// Shared loading/empty/error placeholders used by every screen while it has nothing else to show.
import { h } from "../core/dom.js";
import { icon } from "./icons.js";

export function loading(text) {
  return h("div", { class: "state state--loading", role: "status" },
    h("p", { class: "state__text" }, text || "Loading…"));
}

export function empty(title, body) {
  return h("div", { class: "state state--empty" },
    h("p", { class: "state__title" }, title),
    body ? h("p", { class: "state__text" }, body) : null);
}

function errorMessage(err) {
  if (!err || err.status === 0) return "Can't reach Mira's server.";
  if (err.status === 503) return "History needs the database.";
  return err.detail || "Something went wrong.";
}

export function errorState(err, retry) {
  return h("div", { class: "state state--error" },
    icon("alert-triangle", { label: "" }),
    h("p", { class: "state__title" }, "Something went wrong"),
    h("p", { class: "state__text" }, errorMessage(err)),
    retry ? h("button", { class: "btn btn--secondary", type: "button", onclick: retry }, "Try again") : null);
}
