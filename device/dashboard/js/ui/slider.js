// A labelled native <input type="range">: the Prescribe "When should Mira flag a rep?" card's three
// thresholds. The value is always shown as text (never colour alone) and mirrored into aria-valuetext so a
// screen reader hears "15 percent" rather than the raw "15".
import { h } from "../core/dom.js";

// opts: {label, min, max, step (default 1), value, format(v) -> {text, valueText}, onChange(v)}.
// Returns {el, setValue(v)}; the <input> itself is never rebuilt (only its value/attrs are updated), so a
// slider mid-drag is never yanked out from under the pointer.
export function slider(opts) {
  const { label, min, max, step = 1, format } = opts;
  const fmt = (v) => (typeof format === "function" ? format(v) : { text: String(v), valueText: String(v) });

  const id = `slider-${Math.random().toString(36).slice(2, 9)}`;
  const valueEl = h("output", { class: "slider__value", for: id }, fmt(opts.value).text);
  const input = h("input", {
    id, class: "slider__input", type: "range", min: String(min), max: String(max), step: String(step),
    "aria-label": label,
    oninput: (e) => update(Number(e.target.value), true),
  });
  input.value = String(opts.value);
  input.setAttribute("aria-valuetext", fmt(opts.value).valueText);

  function update(v, fromUser) {
    const clamped = Math.min(max, Math.max(min, v));
    input.value = String(clamped);
    const f = fmt(clamped);
    valueEl.textContent = f.text;
    input.setAttribute("aria-valuetext", f.valueText);
    if (fromUser) opts.onChange?.(clamped);
  }

  const el = h("div", { class: "slider" },
    h("div", { class: "slider__head" }, h("label", { for: id }, label), valueEl),
    input);

  return { el, setValue: (v) => update(v, false), getValue: () => Number(input.value) };
}
