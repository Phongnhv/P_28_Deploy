import { useEffect, useMemo, useState } from "react";
import type { GraphCatalog, GraphKey, GraphNodeSpec, NodeRun, NodeRunDetail } from "../../types";
import { GraphFlow } from "./GraphFlow";
import { NodeDetailDrawer } from "./NodeDetailDrawer";
import { NodeTimeline } from "./NodeTimeline";
import { formatDuration } from "./NodeCard";
import { latestGraphRuns, latestRunsByGraph } from "./graphRunUtils";

/**
 * One page that shows every graph the platform runs.
 *
 * The wizard shows a graph only where it belongs to a step; this page is the
 * whole picture -- including Graph 1 (full) and the dashboard shortcut, which no
 * wizard step owns and which were previously invisible.
 */
export function GraphObservatoryPage({
  catalog,
  runs,
  language,
  loading,
  onRefresh,
  onBack,
  loadNodeDetail,
}: {
  catalog: GraphCatalog | null;
  runs: NodeRun[];
  language: "en" | "vi";
  loading: boolean;
  onRefresh: () => void;
  onBack: () => void;
  loadNodeDetail: (nodeRunId: string) => Promise<NodeRunDetail>;
}) {
  const vi = language === "vi";
  const [selectedGraph, setSelectedGraph] = useState<GraphKey | "ALL">("ALL");
  const [selected, setSelected] = useState<{ node: GraphNodeSpec; run?: NodeRun } | null>(null);

  const visibleGraphs = useMemo(() => {
    if (!catalog) return [];
    return selectedGraph === "ALL"
      ? catalog.graphs
      : catalog.graphs.filter((graph) => graph.key === selectedGraph);
  }, [catalog, selectedGraph]);

  const runsByGraph = useMemo(() => {
    const map = new Map<GraphKey, NodeRun[]>();
    for (const run of runs) {
      const list = map.get(run.graph_key) ?? [];
      list.push(run);
      map.set(run.graph_key, list);
    }
    return map;
  }, [runs]);

  // Keep the drawer's run fresh while a graph is still executing.
  useEffect(() => {
    if (!selected?.run) return;
    const fresh = runs.find((run) => run.id === selected.run?.id);
    if (fresh && fresh.status !== selected.run.status) {
      setSelected((current) => (current ? { ...current, run: fresh } : current));
    }
  }, [runs, selected]);

  const totals = catalog?.totals;
  const latestRuns = latestRunsByGraph(runs);
  const failedCount = latestRuns.filter((run) => run.status === "FAILED").length;
  const runningCount = latestRuns.filter((run) => run.status === "RUNNING").length;
  const totalMs = latestRuns.reduce((sum, run) => sum + run.duration_ms, 0);

  return (
    <div className="graph-observatory">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{vi ? "QUAN SÁT ĐỒ THỊ" : "GRAPH OBSERVATORY"}</span>
          <h2>{vi ? "Mọi node của mọi graph" : "Every node of every graph"}</h2>
          <p>
            {vi
              ? "Từng node agent đã chạy, theo thứ tự nào, mất bao lâu và hỏng ở đâu."
              : "Which agent nodes ran, in what order, how long they took, and where they failed."}
          </p>
        </div>
        <div className="graph-observatory-actions">
          <button className="button ghost" onClick={onRefresh} disabled={loading}>
            {loading ? (vi ? "Đang tải…" : "Loading…") : vi ? "Làm mới" : "Refresh"}
          </button>
          <button className="button ghost" onClick={onBack}>
            ← {vi ? "Quay lại" : "Back"}
          </button>
        </div>
      </div>

      {totals && (
        <div className="graph-observatory-metrics">
          <article>
            <span className="eyebrow">{vi ? "GRAPH" : "GRAPHS"}</span>
            <strong>{totals.graphs}</strong>
          </article>
          <article>
            <span className="eyebrow">{vi ? "NODE" : "NODES"}</span>
            <strong>{totals.nodes}</strong>
            <small>
              {totals.llm_nodes} LLM · {totals.deterministic_nodes}{" "}
              {vi ? "tất định" : "deterministic"}
              {totals.gate_nodes > 0 && ` · ${totals.gate_nodes} ${vi ? "chốt" : "gate"}`}
            </small>
          </article>
          <article>
            <span className="eyebrow">{vi ? "LẦN CHẠY NODE" : "NODE RUNS"}</span>
            <strong>{runs.length}</strong>
            {runningCount > 0 && <small>{runningCount} {vi ? "đang chạy" : "running"}</small>}
          </article>
          <article className={failedCount > 0 ? "graph-metric-danger" : ""}>
            <span className="eyebrow">{vi ? "LỖI" : "FAILED"}</span>
            <strong>{failedCount}</strong>
          </article>
          <article>
            <span className="eyebrow">{vi ? "TỔNG THỜI GIAN" : "TOTAL TIME"}</span>
            <strong>{formatDuration(totalMs)}</strong>
          </article>
        </div>
      )}

      {catalog && (
        <div className="graph-observatory-filters" role="tablist">
          <button
            role="tab"
            aria-selected={selectedGraph === "ALL"}
            className={`graph-filter ${selectedGraph === "ALL" ? "active" : ""}`}
            onClick={() => setSelectedGraph("ALL")}
          >
            {vi ? "Tất cả" : "All"}
          </button>
          {catalog.graphs.map((graph) => {
            const graphRuns = runsByGraph.get(graph.key) ?? [];
            return (
              <button
                key={graph.key}
                role="tab"
                aria-selected={selectedGraph === graph.key}
                className={`graph-filter ${selectedGraph === graph.key ? "active" : ""}`}
                onClick={() => setSelectedGraph(graph.key)}
              >
                {graph.key}
                {graphRuns.length > 0 && <span className="graph-filter-count">{graphRuns.length}</span>}
              </button>
            );
          })}
        </div>
      )}

      {!catalog && !loading && (
        <div className="workflow-artifact-empty">
          {vi ? "Không tải được sơ đồ graph." : "The graph catalog could not be loaded."}
        </div>
      )}

      <div className="graph-observatory-list">
        {visibleGraphs.map((graph) => {
          const graphRuns = latestGraphRuns(runsByGraph.get(graph.key) ?? []);
          return (
            <div className="graph-observatory-card" key={graph.key}>
              <GraphFlow
                graph={graph}
                runs={graphRuns}
                language={language}
                selectedNodeName={selected?.node.name}
                onSelectNode={(node, run) => setSelected({ node, run })}
              />
              {graphRuns.length > 0 && (
                <NodeTimeline
                  runs={graphRuns}
                  language={language}
                  onSelectRun={(run) => {
                    const node = graph.nodes.find((item) => item.name === run.node_name);
                    if (node) setSelected({ node, run });
                  }}
                />
              )}
            </div>
          );
        })}
      </div>

      <NodeDetailDrawer
        node={selected?.node ?? null}
        run={selected?.run}
        language={language}
        onClose={() => setSelected(null)}
        loadDetail={loadNodeDetail}
      />
    </div>
  );
}
