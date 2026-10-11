// Applies a view model (state.js view()) to the DOM. textContent only — captions can be AI-generated.

const $ = (root, id) => root.querySelector(`#${id}`);

function text(root, id, value) {
  const el = $(root, id);
  if (el && el.textContent !== value) el.textContent = value;
}

function show(root, id, visible) {
  const el = $(root, id);
  if (el) el.hidden = !visible;
}

// The Figma copy sets the middle dot off with two spaces on each side ("3 sets  ·  12 reps").
const DOT = "  ·  ";
const spaced = (s) => s.split(" · ").join(DOT);

export function render(root, vm, mascot) {
  const stage = $(root, "stage");
  stage.dataset.screen = vm.screen;
  stage.dataset.mood = vm.mood ?? "none";
  stage.dataset.offline = String(vm.offline);

  const img = $(root, "mira");
  if (vm.mood) {
    const src = mascot.srcFor(vm.mood);
    if (img.getAttribute("src") !== src) img.setAttribute("src", src);
  }

  text(root, "set-label", vm.setLabel ?? "");
  show(root, "set-pill", vm.setLabel !== null);
  text(root, "hr-value", vm.hr === null ? "" : `${Math.round(vm.hr)} bpm`);
  show(root, "hr-chip", vm.hr !== null);

  text(root, "greet-name", vm.firstName ? `Hi, ${vm.firstName}` : "Hi there");
  text(root, "exercise-name", vm.exerciseName);
  text(root, "dose", spaced(vm.dose));

  text(root, "rep-now", String(vm.rep));
  text(root, "rep-target", vm.repsTarget === null ? "" : ` / ${vm.repsTarget}`);
  text(root, "rep-label", vm.exerciseName ? `REPS${DOT}${vm.exerciseName.toUpperCase()}` : "REPS");

  const bar = $(root, "balance");
  show(root, "balance", vm.balance !== null);
  if (vm.balance) {
    bar.dataset.ok = String(vm.balance.ok);
    bar.style.setProperty("--balance-left", `${Math.min(100, Math.max(0, vm.balance.leftPct))}%`);
    text(root, "balance-status", vm.balance.ok ? "EVEN WEIGHT" : "Even out your weight");
  }

  text(root, "countdown-n", vm.countdown === null ? "" : String(vm.countdown));
  const progress = vm.countdown === null ? 0 : Math.min(1, Math.max(0, (4 - vm.countdown) / 3));
  stage.style.setProperty("--countdown-progress", String(progress));
  text(root, "countdown-eyebrow", vm.setLabel ? `GET READY${DOT}${vm.setLabel}` : "GET READY");

  text(root, "paused-progress", vm.repsTarget === null ? "" : `${vm.rep} OF ${vm.repsTarget} REPS DONE`);

  if (vm.summary) {
    text(root, "done-now", String(vm.summary.reps));
    text(root, "done-target", vm.summary.repsTarget === null ? "" : ` / ${vm.summary.repsTarget}`);
    const weight = vm.summary.balanceOk ? "EVEN WEIGHT" : "WEIGHT";
    text(root, "done-balance", vm.summary.balance ? `${weight} ${vm.summary.balance}` : "");
    show(root, "done-pill", vm.summary.balance !== null);
  }

  if (vm.sessionSummary) {
    const { formScore, goodReps, repsTotal, spoken } = vm.sessionSummary;
    text(root, "session-score", formScore === null ? "—" : String(formScore));
    text(root, "session-good", goodReps === null || repsTotal === null ? "" : `${goodReps} of ${repsTotal} good reps`);
    text(root, "session-spoken", spoken);
  }

  text(root, "fall-name", vm.firstName || "the patient");
  text(root, "caption-text", vm.caption);
  show(root, "caption-bar", vm.screen !== "fall" && vm.screen !== "unpaired" && vm.caption !== ""
    && vm.captionsOn !== false);
}
