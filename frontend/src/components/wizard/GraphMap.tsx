import { GraphDef, GraphNodeDef } from "../../graph/registry";
import { TimelineNode } from "../../hooks/useNodeStream";
import { useI18n } from "../../i18n/context";

interface GraphMapProps {
  graph: GraphDef;
  nodesByName?: Record<string, TimelineNode>;
}

export function GraphMap({ graph, nodesByName }: GraphMapProps) {
  const { t } = useI18n();

  const boxW = 145;
  const boxH = 50;
  const gap = 85;
  const pad = 20;
  const ySpine = 25;
  const N = graph.nodes.length;
  const totalW = pad * 2 + N * boxW + (N - 1) * gap;
  const extraH = graph.graphId === "dq_checks" || graph.graphId === "anomaly" ? 75 : 0;
  const totalH = ySpine * 2 + boxH + extraH;

  // Render node or diamond helper
  const renderNode = (n: GraphNodeDef, idx: number) => {
    const x = pad + idx * (boxW + gap);
    const y = ySpine;

    const liveNode = nodesByName?.[n.name];
    const status = liveNode?.status;

    let stroke = "var(--border)";
    let fill = "var(--surface)";
    let strokeWidth = 1.5;
    let strokeDash = "none";
    let isPulse = false;

    if (liveNode && status) {
      stroke = `var(--status-${status})`;
      fill = `color-mix(in srgb, var(--status-${status}) 12%, var(--surface))`;
      strokeWidth = 2;
      if (status === "running") {
        isPulse = true;
        strokeDash = "3 3";
      }
    }

    const textStyle = {
      fill: "var(--ink)",
      fontSize: "12px",
      fontWeight: 600,
      textAnchor: "middle" as const,
      pointerEvents: "none" as const,
    };

    const ownerStyle = {
      fill: "var(--muted)",
      fontSize: "9px",
      textTransform: "uppercase" as const,
      textAnchor: "middle" as const,
      pointerEvents: "none" as const,
    };

    const label = t(n.titleKey) || n.name;
    const ownerText = t(`graph.owner.${n.owner}`) || n.owner;

    if (n.isGate) {
      // Diamond points
      const p1 = `${x + boxW / 2},${y}`;
      const p2 = `${x + boxW},${y + boxH / 2}`;
      const p3 = `${x + boxW / 2},${y + boxH}`;
      const p4 = `${x},${y + boxH / 2}`;
      const points = `${p1} ${p2} ${p3} ${p4}`;

      return (
        <g key={n.name}>
          <polygon
            points={points}
            stroke={stroke}
            fill={fill}
            strokeWidth={strokeWidth}
            strokeDasharray={strokeDash}
            className={isPulse ? "running-pulse" : ""}
          />
          <text x={x + boxW / 2} y={y + boxH / 2 - 2} style={textStyle}>
            {label}
          </text>
          <text x={x + boxW / 2} y={y + boxH / 2 + 12} style={ownerStyle}>
            {ownerText}
          </text>
        </g>
      );
    } else {
      return (
        <g key={n.name}>
          <rect
            x={x}
            y={y}
            width={boxW}
            height={boxH}
            rx={8}
            ry={8}
            stroke={stroke}
            fill={fill}
            strokeWidth={strokeWidth}
            strokeDasharray={strokeDash}
            className={isPulse ? "running-pulse" : ""}
          />
          <text x={x + boxW / 2} y={y + boxH / 2 + 1} style={textStyle}>
            {label}
          </text>
          <text x={x + boxW / 2} y={y + boxH / 2 + 14} style={ownerStyle}>
            {ownerText}
          </text>
        </g>
      );
    }
  };

  // Render sequential arrows
  const renderSpineArrows = () => {
    const arrows = [];
    for (let i = 0; i < N - 1; i++) {
      const x1 = pad + i * (boxW + gap) + boxW;
      const y1 = ySpine + boxH / 2;
      const x2 = pad + (i + 1) * (boxW + gap);
      const y2 = ySpine + boxH / 2;

      // Highlight connection if source and destination are both success/running
      const srcName = graph.nodes[i].name;
      const dstName = graph.nodes[i + 1].name;
      const srcStatus = nodesByName?.[srcName]?.status;
      const dstStatus = nodesByName?.[dstName]?.status;
      
      const isPathActive = srcStatus === "success" && (dstStatus === "success" || dstStatus === "running");
      const stroke = isPathActive ? "var(--accent)" : "var(--border)";
      const strokeWidth = isPathActive ? 2 : 1.5;

      arrows.push(
        <line
          key={`spine-arr-${i}`}
          x1={x1}
          y1={y1}
          x2={x2 - 6} // offset for arrowhead marker
          y2={y2}
          stroke={stroke}
          strokeWidth={strokeWidth}
          markerEnd={`url(#arrowhead-${graph.graphId}-${isPathActive ? "active" : "neutral"})`}
        />
      );
    }
    return arrows;
  };

  // Render branch elements
  const renderBranches = () => {
    if (graph.graphId === "dq_checks") {
      // execute_checks (idx 1) -> __failed__
      const excX = pad + 1 * (boxW + gap);
      const xSrc = excX + boxW / 2;
      const ySrc = ySpine + boxH;
      const yDst = ySrc + 50;

      const isDqFailed = nodesByName?.["execute_checks"]?.status === "failed";
      const branchColor = isDqFailed ? "var(--status-failed)" : "var(--border)";
      const branchStrokeWidth = isDqFailed ? 2 : 1.5;
      const failFill = isDqFailed
        ? "color-mix(in srgb, var(--status-failed) 12%, var(--surface))"
        : "var(--surface)";

      return (
        <g>
          {/* Dashed line to fail node */}
          <line
            x1={xSrc}
            y1={ySrc}
            x2={xSrc}
            y2={yDst - 6}
            stroke={branchColor}
            strokeWidth={branchStrokeWidth}
            strokeDasharray="4 4"
            markerEnd={`url(#arrowhead-${graph.graphId}-${isDqFailed ? "failed" : "neutral"})`}
          />
          {/* Label on line */}
          <text
            x={xSrc + 8}
            y={ySrc + 28}
            style={{ fill: isDqFailed ? "var(--status-failed)" : "var(--muted)", fontSize: "10px", fontWeight: 500 }}
          >
            {t("graph.edges.dqFail") || "on failure"}
          </text>
          {/* Failed terminal node */}
          <rect
            x={xSrc - 50}
            y={yDst}
            width={100}
            height={32}
            rx={6}
            ry={6}
            stroke={branchColor}
            fill={failFill}
            strokeWidth={branchStrokeWidth}
          />
          <text
            x={xSrc}
            y={yDst + 20}
            style={{
              fill: isDqFailed ? "var(--status-failed)" : "var(--ink)",
              fontSize: "11px",
              fontWeight: 700,
              textAnchor: "middle"
            }}
          >
            {t("graph.map.failNode") || "Failed"}
          </text>
        </g>
      );
    }

    if (graph.graphId === "anomaly") {
      // anomaly_detector (idx 0) -> report_writer (idx 3) short-circuit
      const adX = pad + 0 * (boxW + gap);
      const rwX = pad + 3 * (boxW + gap);

      const xSrc = adX + boxW / 2;
      const ySrc = ySpine + boxH;
      const xDst = rwX + boxW / 2;
      const yDst = ySpine + boxH;
      const yBypass = ySrc + 45;

      const adStatus = nodesByName?.["anomaly_detector"]?.status;
      const rwStatus = nodesByName?.["report_writer"]?.status;
      
      // Path is active if anomaly detector was run and did not find anomalies (normal/no historical comparison),
      // leading directly to report writer without hypotheses.
      const isBypassActive = adStatus === "success" && rwStatus === "success" && !nodesByName?.["hypothesis_agent"];
      const branchColor = isBypassActive ? "var(--accent)" : "var(--border)";
      const branchStrokeWidth = isBypassActive ? 2 : 1.5;

      return (
        <g>
          {/* Angled bypass path: down, right, up */}
          <path
            d={`M ${xSrc} ${ySrc} V ${yBypass} H ${xDst} V ${yDst + 6}`}
            fill="none"
            stroke={branchColor}
            strokeWidth={branchStrokeWidth}
            strokeDasharray="4 4"
            markerEnd={`url(#arrowhead-${graph.graphId}-${isBypassActive ? "active" : "neutral"})`}
          />
          {/* Label under path */}
          <text
            x={(xSrc + xDst) / 2}
            y={yBypass + 14}
            style={{
              fill: isBypassActive ? "var(--accent)" : "var(--muted)",
              fontSize: "10px",
              fontWeight: 500,
              textAnchor: "middle"
            }}
          >
            {t("graph.edges.anomalyShortCircuit") || "if not anomalous"}
          </text>
        </g>
      );
    }

    return null;
  };

  return (
    <div style={{ overflowX: "auto", width: "100%", paddingBottom: "8px" }} className="custom-scrollbar">
      <svg
        width={totalW}
        height={totalH}
        viewBox={`0 0 ${totalW} ${totalH}`}
        style={{ display: "block", overflow: "visible" }}
      >
        <defs>
          {/* Standard Arrowheads */}
          <marker
            id={`arrowhead-${graph.graphId}-neutral`}
            markerWidth="6"
            markerHeight="6"
            refX="3"
            refY="3"
            orient="auto"
          >
            <polygon points="0,0 6,3 0,6" fill="var(--border)" />
          </marker>
          <marker
            id={`arrowhead-${graph.graphId}-active`}
            markerWidth="6"
            markerHeight="6"
            refX="3"
            refY="3"
            orient="auto"
          >
            <polygon points="0,0 6,3 0,6" fill="var(--accent)" />
          </marker>
          <marker
            id={`arrowhead-${graph.graphId}-failed`}
            markerWidth="6"
            markerHeight="6"
            refX="3"
            refY="3"
            orient="auto"
          >
            <polygon points="0,0 6,3 0,6" fill="var(--status-failed)" />
          </marker>
        </defs>

        {/* 1. Spine Arrows */}
        {renderSpineArrows()}

        {/* 2. Branch structures (Failed paths / short circuits) */}
        {renderBranches()}

        {/* 3. Graph Nodes */}
        {graph.nodes.map((n, idx) => renderNode(n, idx))}
      </svg>
    </div>
  );
}
