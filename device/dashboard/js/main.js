// Boot: sign-in -> shell. Owns the single ctx object every screen is mounted with, the hash router outlet,
// the sidebar re-render on route/store changes, and sign-out (which tears every listener/socket down).
import { clear } from "./core/dom.js";
import { parseRoute } from "./core/router.js";
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

// Screens land here in later milestones; a route that resolves but has no entry shows "Coming soon".
const SCREENS = {};
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

  const api = createApi({ token, onUnauthorized: () => signOut() });
  const endpoints = createEndpoints(api, therapistId);
  const caps = await loadCapabilities(globalThis.fetch.bind(globalThis));
  const sockets = createSocketHub({ location: globalThis.location, token, therapistId });
  const store = createStore({ proposalsWaiting: 0, selectedPatientId: null, therapist: null, live: null });

  let currentRoute = parseRoute(globalThis.location.hash);
  let unmount = null;

  function renderSidebarInto() {
    clear(sidebarEl);
    sidebarEl.append(renderSidebar({ route: currentRoute, ...store.get() }));
  }

  function render() {
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

  function signOut() {
    globalThis.removeEventListener("hashchange", onHashChange);
    unsubStore();
    unsubEvents();
    unsubNotifications();
    sockets.closeAll();
    unmount?.();
    unmount = null;
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
  try {
    const list = await endpoints.patients();
    const items = (list && list.patients) || [];
    let name = therapistId;
    if (items.length > 0) {
      try {
        const detail = await endpoints.patient(items[0].id);
        const therapistName = detail && detail.patient && detail.patient.therapist_name;
        if (typeof therapistName === "string" && therapistName) name = therapistName;
      } catch { /* keep the id as the display name */ }
    }
    store.set({ therapist: { id: therapistId, name, clinic: "" } });
  } catch {
    store.set({ therapist: { id: therapistId, name: therapistId, clinic: "" } });
  }

  render();
}

const auth = loadAuth(sessionStorage);
if (auth) bootShell(auth);
else showSignIn();
