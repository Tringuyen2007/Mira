// Proposal copy. proposalSummary() turns a proposal's `change` into a short imperative phrase for the
// Caseload Copilot preview; Task 17 adds the full proposal card (approve/edit/dismiss) to this file.

const EXERCISE_PLURAL = {
  squat: "squats", squats: "squats", lunge: "lunges", lunges: "lunges", bridge: "bridges", bridges: "bridges",
};

// Known add-on drill ids -> their display name (hyphenated compound, Figma copy). An id with no entry falls
// back to a plain space-separated reading, which won't always be grammatically ideal but never throws.
const DRILL_NAMES = {
  weight_shift_drill: "weight-shift drill",
};

function pluralExercise(exerciseId) {
  if (!exerciseId) return "the exercise";
  return EXERCISE_PLURAL[exerciseId] || `${exerciseId}s`;
}

function firstSentence(text) {
  const s = String(text || "").trim();
  if (!s) return "";
  const m = s.match(/^.*?[.!?](?=\s|$)/);
  return m ? m[0] : s;
}

export function proposalSummary(proposal) {
  const change = (proposal && proposal.change) || {};
  if (Array.isArray(change.add) && change.add.length > 0) {
    const id = String(change.add[0]);
    const name = DRILL_NAMES[id] || id.replace(/_/g, " ");
    return `add a ${name}`;
  }
  if (change.sets != null && change.reps != null) {
    return `change ${pluralExercise(change.exercise_id)} to ${change.sets} × ${change.reps}`;
  }
  if (change.targets && change.targets.knee_deg != null) {
    return `raise knee bend target to ${Math.round(change.targets.knee_deg)}°`;
  }
  return firstSentence(proposal && proposal.rationale);
}
