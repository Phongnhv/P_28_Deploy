import type { NodeRun } from "../../types";

function runSortKey(run: NodeRun): string {
  return `${run.started_at ?? ""}|${String(run.sequence).padStart(8, "0")}`;
}

/** Return only the newest graph invocation represented in a node-run list. */
export function latestGraphRuns(runs: NodeRun[]): NodeRun[] {
  if (runs.length === 0) return [];
  const latest = runs.reduce((current, run) =>
    runSortKey(run) > runSortKey(current) ? run : current,
  );
  return runs.filter((run) => run.graph_run_id === latest.graph_run_id);
}

/** Keep the newest invocation for every graph key in a mixed list. */
export function latestRunsByGraph(runs: NodeRun[]): NodeRun[] {
  const grouped = new Map<string, NodeRun[]>();
  for (const run of runs) {
    const list = grouped.get(run.graph_key) ?? [];
    list.push(run);
    grouped.set(run.graph_key, list);
  }
  return [...grouped.values()].flatMap((items) => latestGraphRuns(items));
}
