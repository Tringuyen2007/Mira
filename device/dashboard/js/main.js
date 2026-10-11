// Boot: sign-in -> shell. Owns the single ctx object every screen is mounted with, the hash router outlet,
// the sidebar re-render on route/store changes, and sign-out (which tears every listener/socket down).
import { clear, h } from "./core/dom.js";
import { parseRoute, href } from "./core/router.js";
import { createStore } from "./core/store.js";
import { loadAuth, clearAuth } from "./net/auth.js";
import { createApi } from "./net/api.js";
import { createEndpoints } from "./net/endpoints.js";
import { loadCapabilities } from "./net/capabilities.js";
import { createSocketHub } from "./net/sockets.js";
import { renderSidebar } from "./ui/sidebar.js";
import { createToaster } from "./ui/toasts.js";
import { empty } from "./ui/states.js";
import { mountSignIn } from "./screens/signin/index.js";
import { screen as caseloadScreen } from "./screens/caseload/index.js";

// Screens land here in later milestones; a route that resolves but has no entry shows "Coming soon".
const SCREENS = { caseload: caseloadScreen };
const SCREEN_ALIAS = { replay: "patient", proposal: "copilot" };

const appEl = document.getElementById("app");
const signinEl = document.getElementById("signin");
const sidebarEl = document.getElementById("sidebar");
const mainEl = document.getElementById("main");
const toastsEl = document.getElementById("toasts");

const toaster = createToaster(toastsEl);

function showSignIn() {
  appEl.setAttribute("data-state", "signin");
  sidebarEl.hidden = true;
  mainEl.hidden = true;
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
  mainEl.hidden = false;
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

  let currentRoute = parseRoute(globalThis.location.hash);
  let unmount = null;

  function renderSidebarInto() {
    if (!alive) return;
    clear(sidebarEl);
    sidebarEl.append(renderSidebar({ route: currentRoute, ...store.get() }));
  }

  function render() {
    if (!alive) return;
    unmount?.();
    unmount = null;
    clear(mainEl);
    currentRoute = parseRoute(globalThis.location.hash);
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
  const unsubEvents = sockets.subscribe("events", () => { /* live-session/alert screens wire this up later */ });
  const unsubNotifications = sockets.subscribe("notifications", (msg) => {
    if (msg && msg.type === "notification" && typeof msg.unread_count === "number") {
      store.set({ proposalsWaiting: msg.unread_count });
    }
  });

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
    // Clear patient data and any lingering toasts before handing the workstation back to the sign-in screen
    // (shared clinic workstation / projector: nothing from this session should stay on screen).
    clear(mainEl);
    clear(sidebarEl);
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
    navigate: (hashString) => { globalThis.location.hash = hashString; },
    toast: toaster.toast,
    store,
  };

  // Therapist display name: GET /patients/{first} -> patient.therapist_name when available, else the id.
  // Clinic name has no data source yet (server TODO); the sidebar hides the line when it's empty.
  try {
    const list = await endpoints.patients();
    if (!alive) return;
    const items = (list && list.patients) || [];
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
