// One place for approving/editing/dismissing a Mira Assistant proposal and mapping the server's answer to a
// toast, shared by the Assistant rail and the Prescribe rail (final review B, I10). A 409 means someone already
// decided it, so retrying can't help: the caller reloads its list instead of re-enabling the buttons.
//
//   await decideProposal(ctx, () => ctx.api.approveProposal(id), { successText, onConflict, isAlive })
//     -> true on success, false on any failure (the toast is already shown).
export function decisionErrorText(err) {
  const status = err && err.status;
  if (status === 409) return "Already decided";
  if (status === 403) return "Only the patient's therapist can decide this";
  if (status === 400) return err.detail ? `Not saved: ${err.detail}.` : "Check the values and try again.";
  if (status === 422) return "Check the values and try again.";
  return "Couldn't save the decision. Try again.";
}

export async function decideProposal(ctx, call, { successText, onConflict, isAlive = () => true } = {}) {
  try {
    await call();
  } catch (err) {
    if (!isAlive()) return false;
    const conflict = err && err.status === 409;
    ctx.toast({ kind: conflict ? "info" : "error", text: decisionErrorText(err) });
    if (conflict) onConflict?.();
    return false;
  }
  if (isAlive() && successText) ctx.toast({ kind: "success", text: successText });
  return true;
}
