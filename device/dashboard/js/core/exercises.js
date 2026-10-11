// Display names shared by every screen (Prescribe's plan, Patient review's title, Time-lapse, Live), so an
// exercise reads the same everywhere ("Squats", not "squat").

// Known exercise ids -> display name (no server exercise catalog on this branch yet). An id with no entry
// falls back to a humanised reading of the id, so the UI never shows a raw snake_case id.
const EXERCISE_NAMES = {
  squat: "Squats",
  glute_bridge: "Glute bridges",
  single_leg_stance: "Single-leg stance",
  heel_slide: "Heel slides",
  straight_leg_raise: "Straight leg raises",
  sit_to_stand: "Sit to stand",
  step_up: "Step-ups",
};

export function exerciseName(id) {
  if (EXERCISE_NAMES[id]) return EXERCISE_NAMES[id];
  const s = String(id || "").trim();
  if (!s) return "Exercise";
  return s.split(/[_\s]+/).filter(Boolean).map((w, i) => (i === 0 ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
}

// Engine rule ids (reps[].flags, summary.flag_counts keys) -> {noun, plural, side}. "knee_cave_r" reads as
// {noun: "knee cave", side: "right"}; an unknown id falls back to its words.
const FLAG_NOUNS = {
  weight_shift: ["weight shift", "weight shifts"],
  knee_cave: ["knee cave", "knee caves"],
  trunk_lean: ["trunk lean", "trunk leans"],
};

export function describeFlag(flag) {
  const raw = String(flag || "");
  const m = raw.match(/^(.*)_(l|r)$/);
  const base = m ? m[1] : raw;
  const side = m ? (m[2] === "l" ? "left" : "right") : null;
  const [noun, plural] = FLAG_NOUNS[base] || [base.replace(/_/g, " "), `${base.replace(/_/g, " ")}s`];
  return { noun, plural, side };
}

// "2 weight shifts", "1 right knee cave", or "3 flags" when the flags are of different kinds. `counts` is a
// {rule_id: n} map (GET /sessions/{id} summary.flag_counts).
export function flagCountLabel(counts) {
  const entries = Object.entries(counts || {}).filter(([k, n]) => k && Number(n) > 0);
  const total = entries.reduce((a, [, n]) => a + Number(n), 0);
  if (total === 0) return { count: 0, label: "No flags" };
  if (entries.length > 1) return { count: total, label: `${total} flags` };
  const { noun, plural, side } = describeFlag(entries[0][0]);
  const word = total === 1 ? noun : plural;
  return { count: total, label: side ? `${total} ${side} ${word}` : `${total} ${word}` };
}
