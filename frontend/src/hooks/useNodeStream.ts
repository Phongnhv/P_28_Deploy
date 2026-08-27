import { useEffect, useRef, useState } from "react";
import { apiBaseUrl } from "../api/client";
import { isMockMode } from "../api";

/** Lifecycle of a single node (or pseudo-node) in the live timeline. */
export type TimelineNodeStatus = "pending" | "running" | "success" | "failed" | "skipped";

/** One row in the timeline, folded from the SSE event stream
 *  (see src/services/node_event_stream.py for the wire vocabulary). */
export interface TimelineNode {
  /** Backend node / pseudo-node name, e.g. "execute_checks", "anomaly_detector". */
  name: string;
  status: TimelineNodeStatus;
  /** Which graph declared this node (from the `run_start` that announced it).
   *  Undefined for nodes seen before their run_start; grouped as "other". */
  graphId?: string;
  /** Redacted, size-bounded preview of the node's output (set once it finishes). */
  preview?: unknown;
  /** Failure detail (set when status === "failed"). */
  error?: string;
}

export type NodeStreamStatus = "idle" | "connecting" | "streaming" | "done" | "error";

export interface UseNodeStreamResult {
  /** The timeline in announcement order, accumulated across every run_start phase. */
  nodes: TimelineNode[];
  status: NodeStreamStatus;
  error: string | null;
}

/**
 * Subscribe to the per-node SSE stream for a run and fold it into a timeline.
 *
 * Opens an `EventSource` to `GET /api/v1/workflows/{streamId}/stream` while
 * `enabled` is true. Each event upserts the timeline:
 * - `run_start` **appends** any newly announced nodes as *pending* — it never
 *   clears — so a two-graph run (Graph 2's DQ pseudo-nodes followed by Graph 3's
 *   anomaly nodes) accumulates into a single, ordered timeline.
 * - `node_start` flips a node to *running*; `node` to *success* / *failed*.
 * - `run_error` surfaces the failure; `done` closes the stream and demotes any
 *   node still *pending* / *running* to *skipped* (never a stuck spinner).
 *
 * The timeline resets only when the run changes — i.e. `streamId` changes, or the
 * same stream is re-triggered by bumping `runToken`. Auth rides the session cookie
 * (`withCredentials`), matching the GET/CSRF-exempt endpoint. The mock client and
 * non-browser/SSR contexts have nothing to stream and stay idle.
 */
export function useNodeStream(
  streamId: string | null | undefined,
  enabled: boolean,
  runToken?: string | number,
): UseNodeStreamResult {
  const [nodes, setNodes] = useState<TimelineNode[]>([]);
  const [status, setStatus] = useState<NodeStreamStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const doneRef = useRef(false);

  // A new run (different stream id, or the same stream re-triggered via runToken)
  // starts the timeline from scratch. NB: `run_start` events do NOT reset here —
  // they append — so Graph 2 and Graph 3 nodes accumulate within one run.
  useEffect(() => {
    setNodes([]);
    setStatus("idle");
    setError(null);
    doneRef.current = false;
  }, [streamId, runToken]);

  useEffect(() => {
    // SSE is a real-backend feature; the mock client and non-browser/SSR
    // contexts have nothing to stream.
    if (!enabled || !streamId || isMockMode) return;
    if (typeof window === "undefined" || typeof EventSource === "undefined") return;
    // A completed run has already delivered its full timeline; don't reopen just
    // because `enabled` flickered back on.
    if (doneRef.current) return;

    const url = `${apiBaseUrl}/api/v1/workflows/${encodeURIComponent(streamId)}/stream`;
    const source = new EventSource(url, { withCredentials: true });
    setStatus("connecting");

    const parse = <T,>(message: MessageEvent): T => {
      try {
        return (message.data ? JSON.parse(message.data) : {}) as T;
      } catch {
        return {} as T;
      }
    };

    // Insert-or-update a node by name, preserving timeline order. Unknown names
    // (an event before its run_start) are appended so nothing is ever dropped.
    const upsert = (name: string, patch: Partial<TimelineNode>) =>
      setNodes((prev) => {
        const idx = prev.findIndex((node) => node.name === name);
        if (idx === -1) return [...prev, { name, status: "pending", ...patch }];
        const next = prev.slice();
        next[idx] = { ...next[idx], ...patch };
        return next;
      });

    const markStreaming = () => setStatus((prev) => (prev === "error" ? "error" : "streaming"));

    const onRunStart = (message: MessageEvent) => {
      const data = parse<{ nodes?: unknown; graph_id?: unknown }>(message);
      const gid = typeof data.graph_id === "string" ? data.graph_id : undefined;
      const names = Array.isArray(data.nodes)
        ? data.nodes.filter((name): name is string => typeof name === "string")
        : [];
      if (names.length) {
        setNodes((prev) => {
          const declared = new Set(names);
          // Backfill graphId onto any node that arrived before its run_start,
          // then append newly-declared nodes as pending (never clearing).
          const patched = prev.map((node) =>
            gid && declared.has(node.name) && !node.graphId ? { ...node, graphId: gid } : node,
          );
          const seen = new Set(patched.map((node) => node.name));
          const appended = names
            .filter((name) => !seen.has(name))
            .map((name): TimelineNode => ({ name, status: "pending", graphId: gid }));
          const changed = appended.length > 0 || patched.some((node, i) => node !== prev[i]);
          return changed ? [...patched, ...appended] : prev;
        });
      }
      markStreaming();
    };

    const onNodeStart = (message: MessageEvent) => {
      const data = parse<{ node?: string }>(message);
      if (data.node) upsert(data.node, { status: "running" });
      markStreaming();
    };

    const onNode = (message: MessageEvent) => {
      const data = parse<{ node?: string; status?: string; preview?: unknown; error?: string }>(message);
      if (data.node) {
        const nodeStatus: TimelineNodeStatus = data.status === "failed" ? "failed" : "success";
        upsert(data.node, {
          status: nodeStatus,
          ...(data.preview !== undefined ? { preview: data.preview } : {}),
          ...(data.error !== undefined ? { error: data.error } : {}),
        });
      }
      markStreaming();
    };

    const onRunError = (message: MessageEvent) => {
      const data = parse<{ message?: string }>(message);
      setStatus("error");
      setError(data.message ?? "Graph run failed.");
    };

    const onDone = () => {
      doneRef.current = true;
      // Any node still pending/running when the stream closes never reported a
      // terminal result — show it as skipped rather than a perpetual spinner.
      setNodes((prev) =>
        prev.map((node) =>
          node.status === "pending" || node.status === "running" ? { ...node, status: "skipped" } : node,
        ),
      );
      setStatus((prev) => (prev === "error" ? "error" : "done"));
      source.close();
    };

    source.addEventListener("run_start", onRunStart as EventListener);
    source.addEventListener("node_start", onNodeStart as EventListener);
    source.addEventListener("node", onNode as EventListener);
    source.addEventListener("run_error", onRunError as EventListener);
    source.addEventListener("done", onDone as EventListener);

    // Transport-level failure (no `data`). If we already finished, the browser is
    // just noticing the server closed the stream — ignore it.
    source.onerror = () => {
      if (doneRef.current) return;
      setStatus("error");
      setError("Lost connection to the live node stream.");
      source.close();
    };

    return () => {
      source.removeEventListener("run_start", onRunStart as EventListener);
      source.removeEventListener("node_start", onNodeStart as EventListener);
      source.removeEventListener("node", onNode as EventListener);
      source.removeEventListener("run_error", onRunError as EventListener);
      source.removeEventListener("done", onDone as EventListener);
      source.close();
    };
  }, [streamId, enabled, runToken]);

  return { nodes, status, error };
}
