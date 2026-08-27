export type GraphId = "understand" | "propose" | "dq_checks" | "anomaly";
export type NodeOwner = "System" | "Agent" | "Steward" | "Runner";
export type EdgeKind = "step" | "gate" | "branch";

export interface GraphNodeDef {
  name: string;            // backend SSE node id + grouping key (structural)
  graphId: GraphId;
  titleKey: string;        // i18n: "graph.nodes.<name>.title"
  purposeKey: string;      // i18n: "graph.nodes.<name>.purpose" (one line)
  owner: NodeOwner;
  reads: string[];         // structural state-key ids; labelled via "graph.io.<key>" + humanize fallback
  produces: string[];
  isGate?: boolean;        // render as a diamond in the map
}

export interface GraphEdgeDef {
  from: string;
  to: string;
  kind: EdgeKind;
  labelKey?: string;
}

export interface GraphDef {
  graphId: GraphId;
  order: number;           // understand=1 … anomaly=4
  titleKey: string;
  purposeKey: string;
  nodes: GraphNodeDef[];   // ordered = run_start order
  edges: GraphEdgeDef[];
}

export const GRAPHS: GraphDef[] = [
  {
    graphId: "understand",
    order: 1,
    titleKey: "graph.groups.understand.title",
    purposeKey: "graph.groups.understand.purpose",
    nodes: [
      {
        name: "dataset_understanding",
        graphId: "understand",
        titleKey: "graph.nodes.dataset_understanding.title",
        purposeKey: "graph.nodes.dataset_understanding.purpose",
        owner: "Agent",
        reads: ["dataset_id", "dataset_profile_digest"],
        produces: ["semantic_contract"]
      }
    ],
    edges: []
  },
  {
    graphId: "propose",
    order: 2,
    titleKey: "graph.groups.propose.title",
    purposeKey: "graph.groups.propose.purpose",
    nodes: [
      {
        name: "rule_proposer",
        graphId: "propose",
        titleKey: "graph.nodes.rule_proposer.title",
        purposeKey: "graph.nodes.rule_proposer.purpose",
        owner: "Agent",
        reads: ["dataset_id", "semantic_contract"],
        produces: ["proposed_rules"]
      }
    ],
    edges: []
  },
  {
    graphId: "dq_checks",
    order: 3,
    titleKey: "graph.groups.dq_checks.title",
    purposeKey: "graph.groups.dq_checks.purpose",
    nodes: [
      {
        name: "claim_ruleset",
        graphId: "dq_checks",
        titleKey: "graph.nodes.claim_ruleset.title",
        purposeKey: "graph.nodes.claim_ruleset.purpose",
        owner: "Runner",
        reads: ["approved_rules"],
        produces: ["claimed_ruleset"]
      },
      {
        name: "execute_checks",
        graphId: "dq_checks",
        titleKey: "graph.nodes.execute_checks.title",
        purposeKey: "graph.nodes.execute_checks.purpose",
        owner: "Runner",
        reads: ["claimed_ruleset"],
        produces: ["execution_report"],
        isGate: true
      },
      {
        name: "persist_report",
        graphId: "dq_checks",
        titleKey: "graph.nodes.persist_report.title",
        purposeKey: "graph.nodes.persist_report.purpose",
        owner: "System",
        reads: ["execution_report"],
        produces: ["quality_score", "report_id"]
      }
    ],
    edges: [
      { from: "claim_ruleset", to: "execute_checks", kind: "step" },
      { from: "execute_checks", to: "persist_report", kind: "step" },
      { from: "execute_checks", to: "__failed__", kind: "branch", labelKey: "graph.edges.dqFail" }
    ]
  },
  {
    graphId: "anomaly",
    order: 4,
    titleKey: "graph.groups.anomaly.title",
    purposeKey: "graph.groups.anomaly.purpose",
    nodes: [
      {
        name: "anomaly_detector",
        graphId: "anomaly",
        titleKey: "graph.nodes.anomaly_detector.title",
        purposeKey: "graph.nodes.anomaly_detector.purpose",
        owner: "System",
        reads: ["quality_score"],
        produces: ["anomaly_decision"],
        isGate: true
      },
      {
        name: "hypothesis_agent",
        graphId: "anomaly",
        titleKey: "graph.nodes.hypothesis_agent.title",
        purposeKey: "graph.nodes.hypothesis_agent.purpose",
        owner: "Agent",
        reads: ["anomaly_decision"],
        produces: ["hypotheses"]
      },
      {
        name: "persist_analysis",
        graphId: "anomaly",
        titleKey: "graph.nodes.persist_analysis.title",
        purposeKey: "graph.nodes.persist_analysis.purpose",
        owner: "System",
        reads: ["hypotheses"],
        produces: ["analysis_id"]
      },
      {
        name: "report_writer",
        graphId: "anomaly",
        titleKey: "graph.nodes.report_writer.title",
        purposeKey: "graph.nodes.report_writer.purpose",
        owner: "Agent",
        reads: ["hypotheses"],
        produces: ["steward_report_path"]
      }
    ],
    edges: [
      { from: "anomaly_detector", to: "hypothesis_agent", kind: "step" },
      { from: "hypothesis_agent", to: "persist_analysis", kind: "step" },
      { from: "persist_analysis", to: "report_writer", kind: "step" },
      // Note: The LangGraph spine itself is linear (anomaly_detector -> hypothesis_agent -> persist_analysis -> report_writer);
      // this branch is product-semantics representation (NORMAL / INSUFFICIENT_HISTORY short-circuit to report_writer)
      { from: "anomaly_detector", to: "report_writer", kind: "branch", labelKey: "graph.edges.anomalyShortCircuit" }
    ]
  }
];

// Build index maps for fast lookups
export const NODE_INDEX: Record<string, GraphNodeDef> = {};
export const GRAPH_INDEX: Record<string, GraphDef> = {};

GRAPHS.forEach((g) => {
  GRAPH_INDEX[g.graphId] = g;
  g.nodes.forEach((n) => {
    NODE_INDEX[n.name] = n;
  });
});

export const KNOWN_NODE_NAMES: Set<string> = new Set(
  GRAPHS.flatMap((g) => g.nodes.map((n) => n.name))
);

export function nodeDef(name: string): GraphNodeDef | undefined {
  return NODE_INDEX[name];
}

export function graphIdForNode(name: string): GraphId | undefined {
  // Note: persist_report name collision between execution and dq_checks only matters
  // if the dead execution graph is ever streamed. If that happens, switch this index
  // (and useNodeStream's keying) to composite `${graphId}:${name}` keys.
  return NODE_INDEX[name]?.graphId;
}
