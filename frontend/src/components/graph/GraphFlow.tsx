import { useMemo } from "react";
import type { GraphNodeSpec, GraphSpec, NodeRun } from "../../types";
import { NodeCard, formatDuration } from "./NodeCard";
import { latestGraphRuns } from "./graphRunUtils";

/** Pick the run to show for a node: the most recent one wins. */
function latestRunByNode(runs: NodeRun[]): Map<string, NodeRun> {
  const byNode = new Map<string, NodeRun>();
  for (const run of runs) {
    const existing = byNode.get(run.node_name);
    if (!existing) {
      byNode.set(run.node_name, run);
      continue;
    }
    const a = run.started_at ?? "";
    const b = existing.started_at ?? "";
    if (a > b || (a === b && run.sequence > existing.sequence)) byNode.set(run.node_name, run);
  }
  return byNode;
}

export function GraphFlow({
  graph,
  runs,
  language,
  selectedNodeName,
  onSelectNode,
  compact = false,
}: {
  graph: GraphSpec;
  runs: NodeRun[];
  language: "en" | "vi";
  selectedNodeName?: string;
  onSelectNode: (node: GraphNodeSpec, run?: NodeRun) => void;
  compact?: boolean;
}) {
  const runByNode = useMemo(() => latestRunByNode(latestGraphRuns(runs)), [runs]);

  const executed = graph.nodes.filter((node) => runByNode.has(node.name));
  const totalMs = executed.reduce((sum, node) => sum + (runByNode.get(node.name)?.duration_ms ?? 0), 0);
  const failed = executed.filter((node) => runByNode.get(node.name)?.status === "FAILED").length;

  // Conditional edges are what make these graphs interesting -- fail-closed
  // routing, the dictionary bypass. Label them so the branch is legible.
  const conditionFor = (fromName: string, toName: string) => {
    const edge = graph.edges.find((item) => item.from === fromName && item.to === toName);
    if (!edge) return undefined;
    if (language === "vi" && edge.condition_vi) return edge.condition_vi;
    if (edge.condition_en) return edge.condition_en;
    return edge.condition;
  };

  return (
    <section className={`graph-flow ${compact ? "compact" : ""}`} aria-label={language === "vi" ? graph.label_vi : graph.label_en}>
      <header className="graph-flow-head">
        <div>
          <span className="eyebrow">{language === "vi" ? graph.run_vi : graph.run_en}</span>
          <h3>{language === "vi" ? graph.label_vi : graph.label_en}</h3>
          <p>{language === "vi" ? graph.summary_vi : graph.summary_en}</p>
        </div>
        <div className="graph-flow-stats">
          <div>
            <strong>
              {executed.length}/{graph.nodes.length}
            </strong>
            <span>{language === "vi" ? "node đã chạy" : "nodes run"}</span>
          </div>
          <div>
            <strong>{formatDuration(totalMs)}</strong>
            <span>{language === "vi" ? "tổng thời gian" : "total time"}</span>
          </div>
          {failed > 0 && (
            <div className="graph-flow-failed">
              <strong>{failed}</strong>
              <span>{language === "vi" ? "node lỗi" : "failed"}</span>
            </div>
          )}
        </div>
      </header>

      <div className="graph-flow-track" role="list">
        {graph.nodes.map((node, index) => {
          const next = graph.nodes[index + 1];
          const condition = next ? conditionFor(node.name, next.name) : undefined;
          return (
            <div className="graph-flow-item" role="listitem" key={node.name}>
              <NodeCard
                node={node}
                run={runByNode.get(node.name)}
                language={language}
                isSelected={selectedNodeName === node.name}
                onSelect={onSelectNode}
                step={index + 1}
                totalSteps={graph.nodes.length}
              />
              {next && (
                <div className="graph-flow-arrow" aria-hidden="true">
                  <span className="graph-flow-arrow-line" />
                  {condition && <span className="graph-flow-arrow-label">{condition}</span>}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
