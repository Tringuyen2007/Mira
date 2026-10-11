// Sign-in: collects the API token + therapist id, saves them to sessionStorage, then probes GET /patients with
// the new token before handing control back to main.js. A 503 (no database) still means the token is valid.
import { h, clear } from "../../core/dom.js";
import { saveAuth, clearAuth } from "../../net/auth.js";
import { createApi, ApiError } from "../../net/api.js";

export function mountSignIn(root, { storage = globalThis.sessionStorage, fetchImpl = globalThis.fetch?.bind(globalThis),
                                     onSignedIn } = {}) {
  const tokenInput = h("input", { id: "signin-token", name: "token", type: "password", autocomplete: "off",
    spellcheck: "false", required: true });
  const idInput = h("input", { id: "signin-therapist", name: "therapistId", type: "text", autocomplete: "off",
    required: true, value: "okafor" });
  const submitBtn = h("button", { class: "btn btn--primary", type: "submit" }, "Sign in");
  const errorEl = h("p", { class: "signin__error", role: "alert" });

  async function probe(token, therapistId) {
    const api = createApi({ token, fetchImpl });
    try {
      await api.get("/patients");
      return { ok: true };
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 503) return { ok: true }; // no DB yet; the token itself was accepted
        if (err.status === 401) return { ok: false, message: "That token was not accepted" };
        if (err.status === 0) return { ok: false, message: "Can't reach Mira's server" };
        return { ok: false, message: err.detail || "Something went wrong" };
      }
      return { ok: false, message: "Can't reach Mira's server" };
    }
  }

  async function onSubmit(e) {
    e.preventDefault();
    clear(errorEl);
    const token = tokenInput.value.trim();
    const therapistId = idInput.value.trim() || "okafor";
    submitBtn.setAttribute("disabled", "");
    try {
      saveAuth(storage, { token, therapistId });
    } catch {
      errorEl.textContent = "Enter a valid token and therapist id.";
      submitBtn.removeAttribute("disabled");
      return;
    }
    const result = await probe(token, therapistId);
    submitBtn.removeAttribute("disabled");
    if (!result.ok) {
      clearAuth(storage);
      errorEl.textContent = result.message;
      return;
    }
    onSignedIn?.({ token, therapistId });
  }

  const form = h("form", { class: "signin__form", novalidate: true, onsubmit: onSubmit },
    h("h1", { class: "signin__title" }, "Sign in to Mira"),
    h("label", { class: "signin__label", for: "signin-token" }, "API token"),
    tokenInput,
    h("label", { class: "signin__label", for: "signin-therapist" }, "Therapist ID"),
    idInput,
    errorEl,
    submitBtn,
    h("p", { class: "signin__hint" },
      "Your token stays in this browser tab and is cleared when you close it."));

  clear(root);
  root.append(form);
  tokenInput.focus?.();

  return () => { /* nothing to tear down beyond clearing root, which the caller does */ };
}
