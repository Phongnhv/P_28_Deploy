import { useState } from "react";
import { AlertCircle, CheckCircle2, Circle, Loader2, MinusCircle, XCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useI18n } from "../../i18n/context";
import type { TimelineNode, TimelineNodeStatus, NodeStreamStatus } from "../../hooks/useNodeStream";
import { GRAPHS, nodeDef, graphIdForNode, GraphNodeDef, GraphDef } from "../../graph/registry";
import { humanizeNode } from "../../graph/preview";
import { NodeDetailDrawer } from "./NodeDetailDrawer";

interface StatusVisual {
  Icon: LucideIcon;
  color: string;
  labelKey: string;
  spin?: boolean;
}

const STATUS_VISUALS: Record<TimelineNodeStatus, StatusVisual> = {
  pending: { Icon: Circle, color: "var(--status-pending)", labelKey: "nodeStream.statusPending" },
  running: { Icon: Loader2, color: "var(--status-running)", labelKey: "nodeStream.statusRunning", spin: true },
  success: { Icon: CheckCircle2, color: "var(--status-success)", labelKey: "nodeStream.statusSuccess" },
  failed: { Icon: XCircle, color: "var(--status-failed)", labelKey: "nodeStream.statusFailed" },
  skipped: { Icon: MinusCircle, color: "var(--status-skipped)", labelKey: "nodeStream.statusSkipped" },
};

function StatusPill({
  label,
  tone = "neutral",
}: {
  label: string;
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
}) {
  return (
    <span className={`status-pill ${tone}`}>
      <span className="status-dot" />
      {label}
    </span>
  );
}

interface NodeRowProps {
  node: TimelineNode;
  def?: GraphNodeDef;
  onSelect: () => void;
}

function NodeRow({ node, def, onSelect }: NodeRowProps) {
  const { t } = useI18n();
  const visual = STATUS_VISUALS[node.status];
  const { Icon } = visual;

  const title = def ? t(def.titleKey) : humanizeNode(node.name);
  const purpose = def ? t(def.purposeKey) : "";
  const owner = def ? t(`graph.owner.${def.owner}`) : "";

  return (
    <li
      style={{
        display: "flex",
        gap: "12px",
        alignItems: "flex-start",
        padding: "8px 12px",
        borderRadius: "8px",
        transition: "background 0.2s",
      }}
      className="node-row-hover"
    >
      <Icon
        size={18}
        aria-hidden="true"
        style={{
          flex: "0 0 18px",
          color: visual.color,
          marginTop: "3px",
          ...(visual.spin ? { animation: "spin 0.8s linear infinite" } : {}),
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          <button
            type="button"
            onClick={onSelect}
            style={{
              background: "none",
              border: "none",
              padding: 0,
              font: "inherit",
              fontWeight: 700,
              fontSize: "14px",
              color: "var(--ink)",
              cursor: "pointer",
              textAlign: "left",
              textDecoration: "underline",
              textDecorationColor: "transparent",
              transition: "text-decoration-color 0.2s",
            }}
            className="node-title-button"
          >
            {title}
          </button>
          
          <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.03em", color: visual.color }}>
            {t(visual.labelKey)}
          </span>

          {owner && (
            <span className="status-pill gray" style={{ fontSize: "9px", padding: "1px 4px", height: "auto" }}>
              {owner}
            </span>
          )}
        </div>

        {purpose && (
          <p style={{ margin: "4px 0 0", fontSize: "12px", color: "var(--muted)", lineHeight: 1.4 }}>
            {purpose}
          </p>
        )}

        {node.status === "failed" && node.error && (
          <p style={{ margin: "6px 0 0", fontSize: "12px", color: "var(--status-failed)", wordBreak: "break-word" }}>
            {node.error}
          </p>
        )}
      </div>
    </li>
  );
}

interface NodeGroupProps {
  groupId: string;
  groupNodes: TimelineNode[];
  onSelectNode: (name: string) => void;
}

function NodeGroup({ groupId, groupNodes, onSelectNode }: NodeGroupProps) {
  const { t } = useI18n();

  const def = GRAPHS.find((g) => g.graphId === groupId);
  const title = def ? t(def.titleKey) : t(`graph.groups.${groupId}.title`) || groupId;
  const purpose = def ? t(def.purposeKey) : t(`graph.groups.${groupId}.purpose`) || "";

  const total = groupNodes.length;
  const done = groupNodes.filter(
    (n) => n.status === "success" || n.status === "failed" || n.status === "skipped"
  ).length;

  const getGroupState = (nodesList: TimelineNode[]) => {
    if (nodesList.some((n) => n.status === "failed")) {
      return { tone: "danger" as const, label: t("nodeStream.statusFailed") };
    }
    if (nodesList.some((n) => n.status === "running")) {
      return { tone: "info" as const, label: t("nodeStream.statusRunning") };
    }
    if (nodesList.every((n) => n.status === "success" || n.status === "failed" || n.status === "skipped")) {
      return { tone: "success" as const, label: t("nodeStream.completed") || "completed" };
    }
    return { tone: "neutral" as const, label: t("nodeStream.statusPending") };
  };

  const state = getGroupState(groupNodes);

  return (
    <div
      style={{
        padding: "16px",
        background: "var(--surface-muted)",
        border: "1px solid var(--border)",
        borderRadius: "8px",
        marginBottom: "16px",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "8px",
          marginBottom: "12px",
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: "15px", color: "var(--ink)", fontWeight: 700 }}>
            {title}
          </h3>
          {purpose && (
            <p style={{ margin: "2px 0 0", fontSize: "12px", color: "var(--muted)" }}>
              {purpose}
            </p>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--muted)" }}>
            {t("graph.groupProgress", { done, total })}
          </span>
          <StatusPill label={state.label} tone={state.tone} />
        </div>
      </div>

      <ol
        style={{
          listStyle: "none",
          margin: 0,
          padding: 0,
          display: "flex",
          flexDirection: "column",
          gap: "8px",
        }}
      >
        {groupNodes.map((node) => (
          <NodeRow
            key={node.name}
            node={node}
            def={nodeDef(node.name)}
            onSelect={() => onSelectNode(node.name)}
          />
        ))}
      </ol>
    </div>
  );
}

export function NodeTimeline({
  nodes,
  status,
  error,
}: {
  nodes: TimelineNode[];
  status: NodeStreamStatus;
  error: string | null;
}) {
  const { t } = useI18n();
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const total = nodes.length;
  const terminal = nodes.filter(
    (node) => node.status === "success" || node.status === "failed" || node.status === "skipped",
  ).length;
  const failed = nodes.filter((node) => node.status === "failed").length;
  const running = nodes.find((node) => node.status === "running");

  const nodeLabel = (name: string) => {
    const def = nodeDef(name);
    return def ? t(def.titleKey) : humanizeNode(name);
  };

  let summary: string;
  if (status === "error") {
    summary = t("nodeStream.summaryError");
  } else if (total === 0) {
    summary = status === "connecting" ? t("nodeStream.summaryConnecting") : t("nodeStream.waiting");
  } else if (status === "done") {
    summary =
      failed > 0
        ? t("nodeStream.summaryCompleteWithFailures", { done: terminal, total, failed })
        : t("nodeStream.summaryComplete", { total });
  } else if (running) {
    summary = t("nodeStream.summaryRunning", { label: nodeLabel(running.name), done: terminal, total });
  } else {
    summary = t("nodeStream.summaryProgress", { done: terminal, total });
  }

  // Group nodes by graphId in registry order. Unknown/absent graphId -> trailing "other" bucket.
  const groupedNodes: Record<string, TimelineNode[]> = {};
  nodes.forEach((node) => {
    const gid = node.graphId || graphIdForNode(node.name) || "other";
    if (!groupedNodes[gid]) {
      groupedNodes[gid] = [];
    }
    groupedNodes[gid].push(node);
  });

  const orderedGroupIds = [...GRAPHS.map((g) => g.graphId), "other"];
  const activeGroups = orderedGroupIds.filter((gid) => groupedNodes[gid] && groupedNodes[gid].length > 0);

  const activeSelectedNodeObj = nodes.find((n) => n.name === selectedNode) || null;

  return (
    <div
      style={{
        marginTop: "16px",
        padding: "16px",
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "12px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: "12px",
          marginBottom: "12px",
          flexWrap: "wrap",
        }}
      >
        <span className="eyebrow">{t("nodeStream.title")}</span>
        <span
          role="status"
          aria-atomic="true"
          style={{ fontSize: "12px", fontWeight: 600, color: status === "error" ? "#b91c1c" : "var(--muted)" }}
        >
          {summary}
        </span>
      </div>

      {error && (
        <p
          role="alert"
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: "8px",
            margin: "0 0 12px",
            padding: "10px 12px",
            background: "rgba(185,28,28,0.08)",
            border: "1px solid rgba(185,28,28,0.25)",
            borderRadius: "8px",
            fontSize: "13px",
            color: "#b91c1c",
          }}
        >
          <AlertCircle size={16} aria-hidden="true" style={{ flex: "0 0 16px", marginTop: "1px" }} />
          <span>{error}</span>
        </p>
      )}

      {total > 0 ? (
        <>
          {/* Status Legend */}
          <div
            className="status-legend"
            style={{
              display: "flex",
              gap: "16px",
              flexWrap: "wrap",
              marginBottom: "16px",
              padding: "8px 12px",
              background: "var(--surface-muted)",
              borderRadius: "8px",
              border: "1px solid var(--border)",
              alignItems: "center"
            }}
          >
            <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "var(--muted)" }}>
              {t("graph.legend.title") || "Status key"}:
            </span>
            {(Object.keys(STATUS_VISUALS) as TimelineNodeStatus[]).map((statusKey) => {
              const visual = STATUS_VISUALS[statusKey];
              const { Icon } = visual;
              return (
                <div key={statusKey} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <Icon size={14} style={{ color: visual.color, ...(visual.spin ? { animation: "spin 0.8s linear infinite" } : {}) }} />
                  <span style={{ fontSize: "12px", color: "var(--ink-soft)" }}>{t(visual.labelKey)}</span>
                </div>
              );
            })}
          </div>

          {/* Grouped Lists */}
          <div>
            {activeGroups.map((gid) => (
              <NodeGroup
                key={gid}
                groupId={gid}
                groupNodes={groupedNodes[gid]}
                onSelectNode={(name) => setSelectedNode(name)}
              />
            ))}
          </div>
        </>
      ) : (
        !error && (
          <p className="muted" style={{ fontSize: "13px", margin: 0 }}>
            {status === "done" ? t("nodeStream.noEvents") : t("nodeStream.waiting")}
          </p>
        )
      )}

      {/* Drawer */}
      <NodeDetailDrawer
        node={activeSelectedNodeObj}
        def={selectedNode ? nodeDef(selectedNode) : undefined}
        onClose={() => setSelectedNode(null)}
      />
    </div>
  );
}
