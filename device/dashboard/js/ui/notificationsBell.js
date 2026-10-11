// Notifications bell popover: unread badge, latest-20 panel, mark read/ack, "Mark all read", urgent toasts and
// optional desktop alerts. Mounted once at boot into a persistent element (not the sidebar's own re-rendered
// subtree, which is cleared and rebuilt on every route change) so the popover's open/closed state and fetched
// list survive navigation. The sidebar's bell *button* (unread badge, aria-expanded) is still pure-rendered by
// sidebar.js from `getState()`; this module only owns the floating panel.
import { h, clear } from "../core/dom.js";
import { mergeNotifications, linkFor, isUrgent, isUnacked } from "../core/notifications-model.js";

function canUseDesktopNotifications() {
  const w = globalThis.window;
  return Boolean(w && w.isSecureContext && "Notification" in w);
}

export function createNotificationsBell({ panelEl, api, navigate, toast, onChange } = {}) {
  let items = [];
  let unread = 0;
  let urgentUnacked = 0;
  let open = false;
  let triggerEl = null;
  const toastedIds = new Set();
  let desktopPermission = canUseDesktopNotifications() ? globalThis.window.Notification.permission : "unsupported";

  function notifyChange() { onChange?.(); }

  function onKeydown(e) {
    if (e.key === "Escape") close();
  }

  function onDocClick(e) {
    if (panelEl.contains?.(e.target)) return;
    if (triggerEl && triggerEl.contains?.(e.target)) return;
    close();
  }

  function addDocListeners() {
    document.addEventListener("keydown", onKeydown);
    document.addEventListener("click", onDocClick);
  }

  function removeDocListeners() {
    document.removeEventListener("keydown", onKeydown);
    document.removeEventListener("click", onDocClick);
  }

  function desktopNotify(n) {
    if (desktopPermission !== "granted" || !canUseDesktopNotifications()) return;
    const win = globalThis.window;
    // Generic text: OS notification centres keep these after sign-out on a shared workstation, so no patient
    // name or detail goes there (final review B, Minor 10).
    const dn = new win.Notification("Mira: urgent alert", { body: "Open Mira to see the details." });
    dn.onclick = () => {
      win.focus?.();
      const href = linkFor(n);
      if (href) navigate(href);
      dn.close?.();
    };
  }

  function enableDesktopButton() {
    if (!canUseDesktopNotifications() || desktopPermission === "granted") return null;
    return h("button", { class: "btn btn--secondary notif-panel__desktop", type: "button", onclick: () => {
      globalThis.window.Notification.requestPermission().then((perm) => {
        desktopPermission = perm;
        renderPanel();
      });
    } }, "Enable desktop alerts");
  }

  async function markRead(id) {
    const item = items.find((n) => n.id === id);
    if (!item || item.read_at != null) return;
    try {
      await api.markRead(id);
      item.read_at = Date.now() / 1000;
      unread = Math.max(0, unread - 1);
      renderPanel();
      notifyChange();
    } catch { /* leave unread; reopening the panel will retry */ }
  }

  async function ack(id) {
    const item = items.find((n) => n.id === id);
    try {
      const row = await api.ack(id);
      if (item) {
        item.acked_at = row.acked_at;
        item.acked_by = row.acked_by;
        if (isUrgent(item)) urgentUnacked = Math.max(0, urgentUnacked - 1);
      }
      renderPanel();
      notifyChange();
    } catch { /* ignore; the toast/panel action can be retried */ }
  }

  async function markAllRead() {
    try {
      await api.markAllRead();
      const now = Date.now() / 1000;
      for (const n of items) if (n.read_at == null) n.read_at = now;
      unread = 0;
      renderPanel();
      notifyChange();
    } catch { /* ignore */ }
  }

  function openItem(n) {
    markRead(n.id);
    const href = linkFor(n);
    if (href) {
      close();
      navigate(href);
    }
  }

  function notifItem(n) {
    const href = linkFor(n);
    // A real <button>, so Enter and Space open it (final review B, I5); Acknowledge stays a sibling button.
    const row = h("button", { class: `notif-item__row${n.read_at == null ? " notif-item__row--unread" : ""}`,
      type: "button", onclick: () => openItem(n) },
      h("p", { class: "notif-item__title" }, n.title),
      href ? h("span", { class: "notif-item__has-link" }, "Open") : null);
    const ackBtn = (isUrgent(n) && isUnacked(n))
      ? h("button", { class: "btn btn--secondary notif-item__ack", type: "button",
          onclick: (e) => { e.stopPropagation(); ack(n.id); } }, "Acknowledge")
      : null;
    return h("li", { class: "notif-panel__item", "data-id": String(n.id) }, row, ackBtn);
  }

  function renderPanel() {
    clear(panelEl);
    panelEl.hidden = !open;
    if (!open) return;
    const list = items.length === 0
      ? h("p", { class: "notif-panel__empty" }, "No notifications yet.")
      : h("ul", { class: "notif-panel__list" }, ...items.map(notifItem));
    panelEl.append(
      h("div", { class: "notif-panel", role: "dialog", "aria-label": "Notifications", tabindex: "-1" },
        h("div", { class: "notif-panel__head" },
          h("h2", { class: "notif-panel__title" }, "Notifications"),
          h("button", { class: "btn btn--ghost notif-panel__mark-all", type: "button", onclick: markAllRead },
            "Mark all read"),
          h("button", { class: "notif-panel__close", type: "button", "aria-label": "Close",
            onclick: close }, "×")),
        enableDesktopButton(),
        list));
  }

  function toggle(trigger) {
    if (open) { close(); return; }
    triggerEl = trigger;
    open = true;
    renderPanel();
    addDocListeners();
    notifyChange();
    panelEl.querySelector(".notif-panel")?.focus?.();
  }

  function close() {
    if (!open) return;
    open = false;
    removeDocListeners();
    renderPanel();
    notifyChange();
    triggerEl?.focus?.();
  }

  async function init() {
    try {
      const [list, counts] = await Promise.all([api.notifications({ limit: 20 }), api.notificationCount()]);
      items = mergeNotifications([], (list && list.notifications) || []);
      unread = (counts && counts.unread) || 0;
      urgentUnacked = (counts && counts.urgent_unacked) || 0;
    } catch { /* bell starts empty; the WS feed (and a future panel open) will catch up */ }
    notifyChange();
  }

  function handlePush(msg) {
    if (!msg || msg.type !== "notification" || typeof msg.id !== "number") return;
    const alreadyKnown = items.some((n) => n.id === msg.id);
    items = mergeNotifications(items, msg);
    if (!alreadyKnown) {
      if (msg.read_at == null) unread += 1;
      if (isUrgent(msg) && isUnacked(msg)) {
        urgentUnacked += 1;
        if (!toastedIds.has(msg.id)) {
          toastedIds.add(msg.id);
          toast({ kind: "error", text: msg.title, action: { label: "Acknowledge", onClick: () => ack(msg.id) } });
          desktopNotify(msg);
        }
      }
    }
    if (open) renderPanel();
    notifyChange();
  }

  function destroy() {
    removeDocListeners();
    open = false;
    clear(panelEl);
    toastedIds.clear();
  }

  return {
    init, handlePush, toggle, close, destroy,
    getState() { return { unread, urgentUnacked, open }; },
  };
}
