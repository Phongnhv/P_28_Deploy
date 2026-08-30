import type { GraphNodeSpec, NodeRun } from "../../types";

/** Human-readable duration. Sub-second work is noise at second precision. */
export function formatDuration(ms: number): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

const kindLabel: Record<GraphNodeSpec["kind"], string> = {
  LLM: "LLM",
  DETERMINISTIC: "Deterministic",
  GATE: "HITL gate",
};

const statusGlyph: Record<string, string> = {
  SUCCEEDED: "✓",
  FAILED: "✕",
  RUNNING: "●",
  SKIPPED: "–",
};

export function NodeCard({
  node,
  run,
  language,
  isSelected,
  onSelect,
  step,
  totalSteps,
}: {
  node: GraphNodeSpec;
  run?: NodeRun;
  language: "en" | "vi";
  isSelected: boolean;
  onSelect: (node: GraphNodeSpec, run?: NodeRun) => void;
  /** 1-based position in the graph, so the row reads as ordered stages. */
  step?: number;
  totalSteps?: number;
}) {
  const label = language === "vi" ? node.label_vi : node.label_en;
  const purpose = language === "vi" ? node.purpose_vi : node.purpose_en;
  // A node with no run has not executed in this context. That is information,
  // not an error, so it gets its own muted state rather than looking broken.
  const state = run ? run.status.toLowerCase() : "idle";

  return (
    <button
      type="button"
      className={`graph-node-card kind-${node.kind.toLowerCase()} state-${state} ${isSelected ? "selected" : ""}`}
      onClick={() => onSelect(node, run)}
      aria-label={`${label} — ${kindLabel[node.kind]}`}
    >
      <div className="graph-node-top">
        {step !== undefined && (
          <span className="graph-node-step">
            {step}
            {totalSteps ? <i>/{totalSteps}</i> : null}
          </span>
        )}
        <span className={`graph-node-kind kind-${node.kind.toLowerCase()}`}>{kindLabel[node.kind]}</span>
        {run && (
          <span className={`graph-node-status state-${state}`} title={run.status}>
            {statusGlyph[run.status] ?? "•"}
          </span>
        )}
      </div>
      <strong className="graph-node-name">{label}</strong>
      <code className="graph-node-id">{node.name}</code>
      <p className="graph-node-purpose">{purpose}</p>
      <div className="graph-node-foot">
        {run ? (
          <>
            <span className="graph-node-duration">{formatDuration(run.duration_ms)}</span>
            {run.model_name && <span className="graph-node-model">{run.model_name}</span>}
          </>
        ) : (
          <span className="graph-node-duration muted">{language === "vi" ? "Chưa chạy" : "Not run"}</span>
        )}
      </div>
    </button>
  );
}
