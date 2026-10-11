// A numeric −/+ stepper: two buttons around a live value, bounded by [min, max] in steps of `step`. Used by
// the Prescribe editor for sets, reps, rest, knee target and tempo. Keyboard friendly: the buttons are real
// <button>s (tab order, Enter/Space activate them for free); ArrowUp/ArrowDown on the value also step it.
import { h } from "../core/dom.js";

// opts: {label, min, max, step (default 1), value, format(v) -> string shown + aria-valuetext, onChange(v)}.
// Returns {el, setValue(v)} so the host can keep the control in sync with model state without rebuilding it
// (never re-render an input while it's in use).
export function stepper(opts) {
  const { label, min, max, step = 1, format } = opts;
  let value = opts.value;

  const fmt = (v) => (typeof format === "function" ? format(v) : String(v));

  const valueEl = h("span", { class: "stepper__value", role: "status", "aria-live": "off" }, fmt(value));
  const decBtn = h("button", {
    class: "stepper__btn", type: "button", "aria-label": `Decrease ${label}`,
    onclick: () => setValue(value - step, true),
  }, "−");
  const incBtn = h("button", {
    class: "stepper__btn", type: "button", "aria-label": `Increase ${label}`,
    onclick: () => setValue(value + step, true),
  }, "+");

  function clamp(v) {
    return Math.min(max, Math.max(min, v));
  }

  function refresh() {
    valueEl.textContent = fmt(value);
    decBtn.toggleAttribute("disabled", value <= min);
    incBtn.toggleAttribute("disabled", value >= max);
  }

  function setValue(next, fromUser) {
    const clamped = clamp(Math.round(next / step) * step);
    const changed = clamped !== value;
    value = clamped;
    refresh();
    if (fromUser && changed) opts.onChange?.(value);
  }

  function onKeydown(e) {
    if (e.key === "ArrowUp") { e.preventDefault(); setValue(value + step, true); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setValue(value - step, true); }
  }

  const el = h("div", {
    class: "stepper", role: "group", "aria-label": label, tabindex: "0", onkeydown: onKeydown,
  }, decBtn, valueEl, incBtn);

  refresh();
  return { el, setValue: (v) => setValue(v, false), getValue: () => value };
}
