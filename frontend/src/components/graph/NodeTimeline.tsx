import type { NodeRun } from "../../types";
import { formatDuration } from "./NodeCard";

/**
 * Duration bars for one graph run.
 *
 * The point is to make the bottleneck obvious: in these pipelines a single LLM
 * node routinely costs more than every deterministic node combined, and a table
 * of milliseconds hides that.
 */
export function NodeTimeline({
  runs,
  language,
  onSelectRun,
}: {
  runs: NodeRun[];
  language: "en" | "vi";
  onSelectRun?: (run: NodeRun) => void;
}) {
  const ordered = [...runs].sort((a, b) => a.sequence - b.sequence);
  if (ordered.length === 0) return null;

  const longest = Math.max(...ordered.map((run) => run.duration_ms), 1);
  const total = ordered.reduce((sum, run) => sum + run.duration_ms, 0);

  return (
    <div className="graph-timeline">
      <div className="graph-timeline-head">
        <span className="eyebrow">{language === "vi" ? "THỜI LƯỢNG TỪNG NODE" : "NODE DURATIONS"}</span>
        <span className="graph-timeline-total">{formatDuration(total)}</span>
      </div>
      <ul className="graph-timeline-rows">
        {ordered.map((run) => {
          const share = Math.max((run.duration_ms / longest) * 100, 1.5);
          return (
            <li key={run.id}>
              <button
                type="button"
                className="graph-timeline-row"
                onClick={() => onSelectRun?.(run)}
                disabled={!onSelectRun}
              >
                <span className="graph-timeline-label">
                  <span className={`graph-timeline-dot kind-${run.node_kind.toLowerCase()}`} aria-hidden="true" />
                  <code>{run.node_name}</code>
                </span>
                <span className="graph-timeline-bar-track">
                  <span
                    className={`graph-timeline-bar kind-${run.node_kind.toLowerCase()} state-${run.status.toLowerCase()}`}
                    style={{ width: `${share}%` }}
                  />
                </span>
                <span className="graph-timeline-value">{formatDuration(run.duration_ms)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
