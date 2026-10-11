// Boot: sign-in -> shell. Owns the single ctx object every screen is mounted with, the hash router outlet,
// the sidebar re-render on route/store changes, and sign-out (which tears every listener/socket down).
import { applyStoredTheme, getTheme, toggleTheme } from "./ui/theme.js";

// js/theme-boot.js already set <html data-theme> before first paint; this re-applies it (same source) so the
// module's view of the theme matches the document.
applyStoredTheme();

import { clear, h } from "./core/dom.js";
import { parseRoute, href, redirectFor } from "./core/router.js";
import { createStore } from "./core/store.js";
import { loadAuth, clearAuth } from "./net/auth.js";
import { createApi } from "./net/api.js";
import { createEndpoints } from "./net/endpoints.js";
import { loadCapabilities } from "./net/capabilities.js";
import { createSocketHub } from "./net/sockets.js";
import { renderSidebar } from "./ui/sidebar.js";
import { createToaster } from "./ui/toasts.js";
import { createAlertBanner } from "./ui/alertBanner.js";
import { createNotificationsBell } from "./ui/notificationsBell.js";
import { alertsModel, applyAlertEvent } from "./core/alerts-model.js";
import { empty } from "./ui/states.js";
import { mountSignIn } from "./screens/signin/index.js";
import { screen as caseloadScreen } from "./screens/caseload/index.js";
import { screen as assistantScreen } from "./screens/assistant/index.js";
import { screen as patientScreen } from "./screens/patient/index.js";
import { screen as prescribeScreen } from "./screens/prescribe/index.js";
import { screen as liveScreen } from "./screens/live/index.js";
import { screen as timelapseScreen } from "./screens/timelapse/index.js";

// Screens land here in later milestones; a route that resolves but has no entry shows "Coming soon".
const SCREENS = {
  caseload: caseloadScreen, assistant: assistantScreen, patient: patientScreen, prescribe: prescribeScreen,
  live: liveScreen, timelapse: timelapseScreen,
};
const SCREEN_ALIAS = { replay: "patient", proposal: "assistant" };
// Events that mean the pending-proposals count (sidebar's Mira Assistant badge) may have changed.
const PROPOSAL_EVENTS = new Set(["proposal_created", "proposal_decided"]);

const appEl = document.getElementById("app");
const signinEl = document.getElementById("signin");
const sidebarEl = document.getElementById("sidebar");
const contentEl = document.getElementById("content");
const mainEl = document.getElementById("main");
const alertBannerEl = document.getElementById("alertBanner");
const notificationsEl = document.getElementById("notifications");
const toastsEl = document.getElementById("toasts");

const toaster = createToaster(toastsEl);

// The skip link's href="#main" would otherwise be caught by the hash router's own `hashchange` listener (the
// router has no "main" route, so it would land on "Page not found"); intercept the click and just move focus
// to #main (tabindex="-1") instead of letting the browser update location.hash.
document.querySelector(".skip-link")?.addEventListener("click", (e) => {
  e.preventDefault();
  mainEl.focus();
});

function showSignIn() {
  appEl.setAttribute("data-state", "signin");
  sidebarEl.hidden = true;
  contentEl.hidden = true;
  signinEl.hidden = false;
  clear(signinEl);
  mountSignIn(signinEl, {
    storage: sessionStorage,
    onSignedIn: ({ token, therapistId }) => bootShell({ token, therapistId }),
  });
}

async function bootShell({ token, therapistId }) {
  signinEl.hidden = true;
  clear(signinEl);
  sidebarEl.hidden = false;
  contentEl.hidden = false;
  appEl.setAttribute("data-state", "app");

  // Set false (once, idempotently) by signOut. Checked after every await below so a sign-out that happens
  // mid-boot (stale token -> 401 -> onUnauthorized -> signOut) stops this session from resuming and re-mounting
  // a screen, stealing focus from the sign-in form, or re-opening sockets with the now-revoked token.
  let alive = true;
  let signedOut = false;

  const api = createApi({ token, onUnauthorized: () => signOut() });
  const endpoints = createEndpoints(api, therapistId);
  const caps = await loadCapabilities(globalThis.fetch.bind(globalThis));
  if (!alive) return;
  const sockets = createSocketHub({ location: globalThis.location, token, therapistId });
  const store = createStore({ proposalsWaiting: 0, selectedPatientId: null, therapist: null, live: null });

  function navigate(hashString) { globalThis.location.hash = hashString; }

  const alertBanner = createAlertBanner(alertBannerEl, { resolveAlert: endpoints.resolveAlert });
  const bell = createNotificationsBell({
    panelEl: notificationsEl, api: endpoints, navigate, toast: toaster.toast, onChange: renderSidebarInto,
  });
  let alertsState = [];
  let theme = getTheme();

  let currentRoute = parseRoute(globalThis.location.hash);
  let unmount = null;

  function onThemeToggle() {
    theme = toggleTheme();
    renderSidebarInto();
  }

  function renderSidebarInto() {
    if (!alive) return;
    clear(sidebarEl);
    const bellState = bell.getState();
    sidebarEl.append(renderSidebar({
      route: currentRoute, ...store.get(),
      unreadCount: bellState.unread, bellOpen: bellState.open, onBellToggle: (btn) => bell.toggle(btn),
      theme, onThemeToggle,
    }));
  }

  // Sidebar badge (decisions): pending proposals only, never the unread-notification count — loaded once at
  // boot and refreshed on proposal_created/proposal_decided events, independent of the notifications bell.
  async function refreshProposalsWaiting() {
    if (!alive) return;
    try {
      const res = await endpoints.proposals({ status: "pending" });
      if (!alive) return;
      store.set({ proposalsWaiting: ((res && res.proposals) || []).length });
    } catch { /* leave the last known count on a transient failure */ }
  }

  function render() {
    if (!alive) return;
    unmount?.();
    unmount = null;
    clear(mainEl);
    currentRoute = parseRoute(globalThis.location.hash);
    // Old links (#/copilot) forward to their new route; replace() fires hashchange, which renders again.
    const redirect = redirectFor(currentRoute);
    if (redirect) {
      globalThis.location.replace(redirect);
      return;
    }
    if (currentRoute.params.pid) store.set({ selectedPatientId: currentRoute.params.pid });
    renderSidebarInto();
    const key = SCREEN_ALIAS[currentRoute.name] || currentRoute.name;
    const screen = SCREENS[key];
    if (screen && typeof screen.mount === "function") {
      unmount = screen.mount(mainEl, ctx, currentRoute) || null;
    } else if (currentRoute.name === "notfound") {
      mainEl.append(
        empty("Page not found", "That page doesn't exist."),
        h("a", { class: "btn btn--secondary", href: href("caseload") }, "Go to caseload"));
    } else {
      mainEl.append(empty("Coming soon", ""));
    }
    mainEl.focus();
  }

  const unsubStore = store.subscribe(renderSidebarInto);
  const unsubEvents = sockets.subscribe("events", (msg) => {
    if (!msg) return;
    if (msg.type === "alert") {
      alertsState = applyAlertEvent(alertsState, msg);
      alertBanner.render(alertsState);
    } else if (PROPOSAL_EVENTS.has(msg.type)) {
      refreshProposalsWaiting();
    }
  });
  const unsubNotifications = sockets.subscribe("notifications", (msg) => bell.handlePush(msg));

  function onHashChange() { render(); }
  globalThis.addEventListener("hashchange", onHashChange);

  // Idempotent: a stale token can produce several concurrent 401s (patients list + patient detail, say), each
  // calling onUnauthorized -> signOut(); only the first should tear anything down or touch the DOM.
  function signOut() {
    if (signedOut) return;
    signedOut = true;
    alive = false;
    globalThis.removeEventListener("hashchange", onHashChange);
    unsubStore();
    unsubEvents();
    unsubNotifications();
    sockets.closeAll();
    unmount?.();
    unmount = null;
    // Clear patient data and any lingering toasts/alerts/notifications before handing the workstation back to
    // the sign-in screen (shared clinic workstation / projector: nothing from this session should stay on screen).
    clear(mainEl);
    clear(sidebarEl);
    alertsState = [];
    alertBanner.render([]);
    bell.destroy();
    toaster.clearAll();
    clearAuth(sessionStorage);
    globalThis.location.hash = "";
    showSignIn();
  }

  const ctx = {
    api: endpoints,
    sockets,
    auth: { therapistId, signOut },
    caps,
    navigate,
    toast: toaster.toast,
    store,
  };

  bell.init();

  // Therapist display name: GET /patients/{first} -> patient.therapist_name when available, else the id.
  // Clinic name has no data source yet (server TODO); the sidebar hides the line when it's empty. Also builds
  // patientsById for the alerts banner's "Fall — <name>" title.
  let patientsById = {};
  try {
    const list = await endpoints.patients();
    if (!alive) return;
    const items = (list && list.patients) || [];
    patientsById = Object.fromEntries(items.map((p) => [p.id, p]));
    let name = therapistId;
    if (items.length > 0) {
      try {
        const detail = await endpoints.patient(items[0].id);
        if (!alive) return;
        const therapistName = detail && detail.patient && detail.patient.therapist_name;
        if (typeof therapistName === "string" && therapistName) name = therapistName;
      } catch { /* keep the id as the display name */ }
    }
    if (!alive) return;
    store.set({ therapist: { id: therapistId, name, clinic: "" } });
  } catch {
    if (alive) store.set({ therapist: { id: therapistId, name: therapistId, clinic: "" } });
  }

  if (!alive) return;
  render();

  refreshProposalsWaiting();
  try {
    const alertsJson = await endpoints.alerts();
    if (!alive) return;
    alertsState = alertsModel(alertsJson, patientsById);
    alertBanner.render(alertsState);
  } catch { /* the open-alerts banner stays empty; a live `alert` event can still populate it */ }
}

// Clickjacking: frame-ancestors can't be set in a <meta> CSP and the server doesn't send X-Frame-Options for
// /dashboard/, so refuse to boot the token-entry page inside a frame instead (no-op outside a browser, e.g. tests).
if (typeof globalThis.top !== "undefined" && typeof globalThis.self !== "undefined" && globalThis.top !== globalThis.self) {
  clear(document.body);
} else {
  const auth = loadAuth(sessionStorage);
  if (auth) bootShell(auth);
  else showSignIn();
}
