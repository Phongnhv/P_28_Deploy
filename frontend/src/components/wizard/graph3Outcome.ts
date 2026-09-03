import type { AgentArtifact } from "../../types";

/** Read the completed report for this execution, never infer it from filtered signals. */
export function graph3Decision(artifacts: AgentArtifact[], executionId?: string): string | null {
  if (!executionId) return null;
  for (const artifact of [...artifacts].reverse()) {
    if (artifact.type !== "ANOMALY_REPORT" || artifact.status === "STALE" || artifact.temporary) continue;
    const payload = artifact.payload as Record<string, unknown> | null;
    if (payload?.execution_run_id === executionId && payload.status === "SUCCEEDED"
      && typeof payload.decision === "string") return payload.decision;
  }
  return null;
}
