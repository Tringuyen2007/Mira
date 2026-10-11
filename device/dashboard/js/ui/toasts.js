// Toaster: up to 3 visible toasts, auto-dismiss after 6s (errors persist until closed).
import { h, clear } from "../core/dom.js";

const AUTO_DISMISS_MS = 6000;
const MAX_VISIBLE = 3;

export function createToaster(container) {
  let queue = []; // { id, el, timer }
  let nextId = 1;

  function render() {
    clear(container);
    for (const item of queue.slice(0, MAX_VISIBLE)) container.append(item.el);
  }

  function dismiss(id) {
    const i = queue.findIndex((t) => t.id === id);
    if (i === -1) return;
    clearTimeout(queue[i].timer);
    queue.splice(i, 1);
    render();
  }

  function toast({ kind = "info", text, action } = {}) {
    const id = nextId++;
    const el = h("div", { class: `toast toast--${kind}` },
      h("p", { class: "toast__text" }, text),
      action
        ? h("button", { class: "toast__action", type: "button", onclick: () => { action.onClick?.(); dismiss(id); } },
            action.label)
        : null,
      h("button", { class: "toast__close", type: "button", "aria-label": "Close", onclick: () => dismiss(id) }, "×"));
    const entry = { id, el, timer: null };
    queue.unshift(entry);
    if (kind !== "error") entry.timer = setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    render();
    return id;
  }

  function clearAll() {
    for (const item of queue) clearTimeout(item.timer);
    queue = [];
    render();
  }

  return { toast, dismiss, clearAll };
}
