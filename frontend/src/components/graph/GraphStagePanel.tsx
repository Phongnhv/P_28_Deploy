import { useEffect, useMemo, useState } from "react";
import type { GraphCatalog, GraphKey, GraphNodeSpec, NodeRun, NodeRunDetail } from "../../types";
import { GraphFlow } from "./GraphFlow";
import { NodeDetailDrawer } from "./NodeDetailDrawer";
import { NodeTimeline } from "./NodeTimeline";

/**
 * The embeddable form of the graph view, for a single wizard step.
 *
 * Owns the drawer so each host step in App.tsx only has to say which graphs it
 * cares about; collapsing is per-panel so a step showing two graphs (Run 2 and
 * Run 3) does not force both open at once.
 */
export function GraphStagePanel({
  catalog,
  runs,
  graphKeys,
  language,
  loadNodeDetail,
  defaultOpen = false,
  showTimeline = true,
  emptyNote,
}: {
  catalog: GraphCatalog | null;
  runs: NodeRun[];
  graphKeys: GraphKey[];
  language: "en" | "vi";
  loadNodeDetail: (nodeRunId: string) => Promise<NodeRunDetail>;
  defaultOpen?: boolean;
  showTimeline?: boolean;
  /** Shown when this graph has no runs, to explain why rather than leave a row
      of "not run" cards next to results that plainly exist. */
  emptyNote?: string;
}) {
  const vi = language === "vi";
  const [open, setOpen] = useState(defaultOpen);
  const [selected, setSelected] = useState<{ node: GraphNodeSpec; run?: NodeRun } | null>(null);

  const graphs = useMemo(
    () => (catalog ? catalog.graphs.filter((graph) => graphKeys.includes(graph.key)) : []),
    [catalog, graphKeys],
  );

  // While a graph is mid-run the list refreshes underneath us; keep the open
  // drawer pointing at the live row rather than a stale snapshot.
  useEffect(() => {
    if (!selected?.run) return;
    const fresh = runs.find((run) => run.id === selected.run?.id);
    if (fresh && fresh.status !== selected.run.status) {
      setSelected((current) => (current ? { ...current, run: fresh } : current));
    }
  }, [runs, selected]);

  if (graphs.length === 0) return null;

  const relevantRuns = runs.filter((run) => graphKeys.includes(run.graph_key));
  const running = relevantRuns.some((run) => run.status === "RUNNING");
  const failed = relevantRuns.filter((run) => run.status === "FAILED").length;

  return (
    <section className="graph-stage-panel">
      <button
        type="button"
        className="graph-stage-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="eyebrow">{vi ? "CHI TIẾT NODE AGENT" : "AGENT NODE DETAIL"}</span>
        <span className="graph-stage-toggle-meta">
          {running && <span className="graph-stage-live">{vi ? "đang chạy" : "running"}</span>}
          {failed > 0 && (
            <span className="graph-stage-failed">
              {failed} {vi ? "lỗi" : "failed"}
            </span>
          )}
          <span className="graph-stage-chevron">{open ? "▾" : "▸"}</span>
        </span>
      </button>

      {open && (
        <div className="graph-stage-body">
          {emptyNote && relevantRuns.length === 0 && (
            <p className="graph-stage-note">{emptyNote}</p>
          )}
          {graphs.map((graph) => {
            const graphRuns = relevantRuns.filter((run) => run.graph_key === graph.key);
            return (
              <div className="graph-stage-graph" key={graph.key}>
                <GraphFlow
                  graph={graph}
                  runs={graphRuns}
                  language={language}
                  selectedNodeName={selected?.node.name}
                  onSelectNode={(node, run) => setSelected({ node, run })}
                  compact
                />
                {showTimeline && graphRuns.length > 0 && (
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
      )}

      <NodeDetailDrawer
        node={selected?.node ?? null}
        run={selected?.run}
        language={language}
        onClose={() => setSelected(null)}
        loadDetail={loadNodeDetail}
      />
    </section>
  );
}
