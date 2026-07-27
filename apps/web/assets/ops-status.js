export function opsStatusbarModel({
  hasRun,
  failed,
  terminal,
  pendingApproval,
  stageStates,
}) {
  const score = stageStates.reduce((total, stageState) => {
    if (stageState === "done") {
      return total + 1;
    }
    if (stageState === "active" || stageState === "failed") {
      return total + 0.5;
    }
    return total;
  }, 0);
  const progress = !hasRun
    ? 0
    : terminal && !failed
      ? 100
      : Math.round((score / stageStates.length) * 100);

  let dotState = "idle";
  if (!hasRun) {
    dotState = "idle";
  } else if (pendingApproval) {
    dotState = "warn";
  } else if (failed) {
    dotState = "error";
  } else if (terminal) {
    dotState = "done";
  } else {
    dotState = "live";
  }

  return {
    progress,
    progressState: failed ? "error" : terminal ? "done" : "running",
    dotState,
  };
}
