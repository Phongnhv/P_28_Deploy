import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from "react";
import { X } from "lucide-react";
import { api } from "../../api";
import type { Dataset, Graph1NodeExecution, Graph1NodeStatus, Graph1RuleDecision, Graph1Run } from "../../types";
import { buildDisplayStages, EvidenceOverview, stageDuration, stageEvidence, StagePresenter } from "./presenters";
import "./graph1-studio.css";

type SidebarTab = "output" | "evidence" | "activity";
const statusTone = (status: Graph1NodeStatus) => status === "SUCCEEDED" ? "success" : status === "FAILED" ? "danger" : "warning";
const human = (value: string) => value.replaceAll("_", " ");
type Row = Record<string, any>;
type Decision = "undecided" | "approve" | "edit" | "reject";
const record = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const rulesFromNodes = (nodes: Graph1NodeExecution[]) => {
  const value = nodes.find((node) => node.node_key === "rule_proposer")?.output.proposed_rules;
  return Array.isArray(value) ? value.filter((item): item is Row => Boolean(item && typeof item === "object" && !Array.isArray(item))) : [];
};

export function Graph1DetailsSidebar({ run, nodes, dataset, onClose, canOperate, onRefresh }: { run: Graph1Run | null; nodes: Graph1NodeExecution[]; dataset?: Dataset; onClose: () => void; canOperate: boolean; onRefresh: (runId: string) => Promise<unknown> }) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const resizeStartRef = useRef<{ axis: "width" | "height"; startX: number; startY: number; startWidth: number; startHeight: number } | null>(null);
  const stages = useMemo(() => buildDisplayStages(nodes), [nodes]);
  const [sidebarWidth, setSidebarWidth] = useState(() => Math.max(360, Math.round(window.innerWidth * 0.4)));
  const [sidebarHeight, setSidebarHeight] = useState(() => Math.max(320, window.innerHeight - 158));
  const [selectedKey, setSelectedKey] = useState("profile_info");
  const [activeTab, setActiveTab] = useState<SidebarTab>("output");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [semanticText, setSemanticText] = useState("");
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [ruleEdits, setRuleEdits] = useState<Record<string, Record<string, string>>>({});
  const selected = stages.find((stage) => stage.key === selectedKey) ?? stages[0];
  const selectedEvidence = useMemo(() => selected ? stageEvidence(selected) : [], [selected]);
  const rules = useMemo(() => rulesFromNodes(nodes), [nodes]);
  const semanticDraft = useMemo(() => { try { return record(JSON.parse(semanticText || "{}")); } catch { return {}; } }, [semanticText]);

  useEffect(() => {
    const semantic = nodes.find((node) => node.node_key === "dataset_understanding")?.output.semantic_contract;
    if (run?.status === "AWAITING_SEMANTIC_REVIEW" && semantic && !semanticText) setSemanticText(JSON.stringify(semantic, null, 2));
  }, [run?.status, nodes, semanticText]);
  useEffect(() => {
    if (!rules.length) return;
    setDecisions((current) => Object.keys(current).length ? current : Object.fromEntries(rules.map((rule, index) => [String(rule.rule_id ?? `rule-${index}`), "undecided"])));
    setRuleEdits((current) => Object.keys(current).length ? current : Object.fromEntries(rules.map((rule, index) => { const id = String(rule.rule_id ?? `rule-${index}`); const params = record(rule.parameters); return [id, { type: String(rule.rule_type ?? ""), name: String(rule.rule_name ?? ""), description: String(rule.rule_description ?? ""), column: String(rule.column ?? ""), min_value: String(params.min_value ?? ""), max_value: String(params.max_value ?? "") }]; })));
  }, [rules]);

  useEffect(() => setActiveTab("output"), [selectedKey]);
  useEffect(() => {
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    const sidebarTop = () => window.innerWidth <= 700 ? 112 : window.innerWidth <= 1100 ? 142 : 158;
    const clampWidth = (value: number) => Math.min(Math.max(value, 360), Math.max(360, window.innerWidth - 320));
    const clampHeight = (value: number) => Math.min(Math.max(value, 320), Math.max(320, window.innerHeight - sidebarTop() - 8));
    const handlePointerMove = (event: PointerEvent) => {
      const start = resizeStartRef.current;
      if (!start) return;
      if (start.axis === "width") setSidebarWidth(clampWidth(start.startWidth + (start.startX - event.clientX)));
      else setSidebarHeight(clampHeight(start.startHeight + (event.clientY - start.startY)));
    };
    const handlePointerUp = () => { resizeStartRef.current = null; document.body.style.userSelect = ""; };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      document.body.style.userSelect = "";
    };
  }, []);

  const resizeByKeyboard = (axis: "width" | "height", event: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? 80 : 24;
    const max = Math.max(360, window.innerWidth - 320);
    const sidebarTop = window.innerWidth <= 700 ? 112 : window.innerWidth <= 1100 ? 142 : 158;
    const maxHeight = Math.max(320, window.innerHeight - sidebarTop - 8);
    const keys = axis === "width" ? ["ArrowLeft", "ArrowRight", "Home", "End"] : ["ArrowUp", "ArrowDown", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    if (axis === "width") setSidebarWidth((current) => event.key === "Home" ? 360 : event.key === "End" ? max : Math.min(Math.max(current + (event.key === "ArrowLeft" ? step : -step), 360), max));
    else setSidebarHeight((current) => event.key === "Home" ? 320 : event.key === "End" ? maxHeight : Math.min(Math.max(current + (event.key === "ArrowDown" ? step : -step), 320), maxHeight));
  };

  const updateSemanticColumn = (tableKey: string, index: number, field: string, value: string | boolean | number) => {
    const next = structuredClone(semanticDraft); const tables = record(next.tables); const table = record(tables[tableKey]); const columns = Array.isArray(table.columns) ? [...table.columns] as Row[] : [];
    columns[index] = { ...record(columns[index]), [field]: value }; table.columns = columns; tables[tableKey] = table; next.tables = tables; setSemanticText(JSON.stringify(next, null, 2));
  };
  const confirmSemantic = async () => {
    if (!run) return; setBusy(true); setActionError("");
    try { await api.confirmGraph1Semantic(run.id, semanticDraft); await onRefresh(run.id); setSelectedKey("rule_candidate_builder"); }
    catch (error) { setActionError(error instanceof Error ? error.message : "Unable to approve the Semantic Contract."); }
    finally { setBusy(false); }
  };
  const confirmRules = async () => {
    if (!run || !rules.length) return;
    const pending = rules.filter((rule, index) => !decisions[String(rule.rule_id ?? `rule-${index}`)] || decisions[String(rule.rule_id ?? `rule-${index}`)] === "undecided").length;
    if (pending || !Object.values(decisions).some((value) => value === "approve" || value === "edit")) { setActionError(pending ? `${pending} rule${pending === 1 ? " is" : "s are"} still awaiting a decision.` : "Approve at least one rule to continue."); return; }
    setBusy(true); setActionError("");
    try {
      const payload = rules.map((rule, index) => { const id = String(rule.rule_id ?? `rule-${index}`); const action = decisions[id] as Graph1RuleDecision["action"]; const draft = ruleEdits[id] ?? {}; return { rule_id: id, action, ...(action === "edit" ? { rule: { type: draft.type, rule_name: draft.name, rule_description: draft.description, column: draft.column || null, parameters: { min_value: draft.min_value === "" ? undefined : Number(draft.min_value), max_value: draft.max_value === "" ? undefined : Number(draft.max_value) } } } : {}) }; });
      await api.reviewGraph1Rules(run.id, payload); await onRefresh(run.id); setSelectedKey("hitl_gate");
    } catch (error) { setActionError(error instanceof Error ? error.message : "Unable to save rule approvals."); }
    finally { setBusy(false); }
  };

  const reviewPanel = selected?.key === "hitl_gate" && run?.status === "AWAITING_RULE_REVIEW" && canOperate ? <div className="graph1-sidebar-review"><div><strong>Rule approval required</strong><span>Choose a decision for every persisted proposal, then approve to continue.</span></div>{rules.map((rule, index) => { const id = String(rule.rule_id ?? `rule-${index}`); return <label key={id}><span>{id} · {String(rule.rule_name ?? rule.rule_description ?? "Rule proposal")}</span><select value={decisions[id] ?? "undecided"} onChange={(event) => setDecisions((current) => ({ ...current, [id]: event.target.value as Decision }))}><option value="undecided">Choose decision</option><option value="approve">Approve</option><option value="edit">Edit & approve</option><option value="reject">Reject</option></select></label>; })}<button type="button" className="button primary" disabled={busy} onClick={() => void confirmRules()}>{busy ? "Saving…" : "Approve and continue"}</button>{actionError && <p role="alert">{actionError}</p>}</div> : null;

  return <aside id="graph1-details-sidebar" className="g1-studio graph1-details-sidebar" style={{ "--graph1-sidebar-width": `${sidebarWidth}px`, "--graph1-sidebar-height": `${sidebarHeight}px` } as CSSProperties} aria-label="Agent Execution Studio node details">
    <div
      className="graph1-sidebar-resize-handle graph1-sidebar-width-handle"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize node details sidebar"
      aria-valuemin={360}
      aria-valuemax={Math.max(360, window.innerWidth - 320)}
      aria-valuenow={sidebarWidth}
      tabIndex={0}
      title="Drag to resize sidebar"
      onKeyDown={(event) => resizeByKeyboard("width", event)}
      onPointerDown={(event) => {
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        resizeStartRef.current = { axis: "width", startX: event.clientX, startY: event.clientY, startWidth: sidebarWidth, startHeight: sidebarHeight };
        document.body.style.userSelect = "none";
      }}
    ><span aria-hidden="true" /></div>
    <div
      className="graph1-sidebar-resize-handle graph1-sidebar-height-handle"
      role="separator"
      aria-orientation="horizontal"
      aria-label="Resize node details sidebar height"
      aria-valuemin={320}
      aria-valuemax={Math.max(320, window.innerHeight - (window.innerWidth <= 700 ? 112 : window.innerWidth <= 1100 ? 142 : 158) - 8)}
      aria-valuenow={sidebarHeight}
      tabIndex={0}
      title="Drag to resize sidebar height"
      onKeyDown={(event) => resizeByKeyboard("height", event)}
      onPointerDown={(event) => {
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        resizeStartRef.current = { axis: "height", startX: event.clientX, startY: event.clientY, startWidth: sidebarWidth, startHeight: sidebarHeight };
        document.body.style.userSelect = "none";
      }}
    ><span aria-hidden="true" /></div>
    <header className="graph1-sidebar-header"><div><span className="eyebrow">AGENT EXECUTION STUDIO</span><h2>Node details</h2><p>{dataset?.name ?? run?.dataset_id ?? "Agent Workflow"}</p></div><button ref={closeButtonRef} type="button" className="button ghost graph1-sidebar-close" onClick={onClose} aria-label="Close node details" title="Close node details"><X aria-hidden="true" /></button></header>
    <div className="graph1-sidebar-status" role="status"><span className={`g1-chip ${run ? statusTone(run.status === "COMPLETED" ? "SUCCEEDED" : run.status === "FAILED" ? "FAILED" : "RUNNING") : "warning"}`}>{run ? human(run.status) : "No run selected"}</span><span>{run?.current_node ? `Current node: ${run.current_node}` : "Showing persisted outputs only"}</span></div>
    <div className="graph1-sidebar-body"><div className="g1-workspace graph1-sidebar-workspace">
      <nav className="g1-node-rail" aria-label="Agent Workflow display stages"><div className="g1-rail-heading"><div><span>EXECUTION PATH</span><strong>{stages.length} display stages · {nodes.length} backend nodes</strong></div></div><ol>{stages.map((stage) => <li key={stage.key} className={stage.status.toLowerCase()}><button type="button" className={selected?.key === stage.key ? "active" : ""} aria-current={selected?.key === stage.key ? "step" : undefined} onClick={() => setSelectedKey(stage.key)}><span className="g1-node-state">{stage.status === "SUCCEEDED" ? "✓" : stage.canonicalLabel}</span><span className="g1-node-copy"><span>{stage.canonicalLabel} · {stage.key}</span><strong>{stage.title}</strong><small>{human(stage.status)}</small></span><span className="g1-node-chevron" aria-hidden="true">›</span></button></li>)}</ol></nav>
      <section className="g1-output" aria-live="polite">{selected ? <><header className="g1-output-header"><div className="g1-output-title"><div className="g1-icon-tile">{selected.canonicalLabel}</div><div><span>STAGE {selected.canonicalLabel} OUTPUT</span><h2>{selected.title}</h2><p>{selected.description}</p></div></div><div className="g1-output-status"><span className={`g1-chip ${statusTone(selected.status)}`}>{human(selected.status)}</span><small>{stageDuration(selected)}</small></div></header><nav className="g1-output-tabs" aria-label={`${selected.title} views`}>{(["output", "evidence", "activity"] as SidebarTab[]).map((tab) => <button type="button" className={activeTab === tab ? "active" : ""} aria-pressed={activeTab === tab} key={tab} onClick={() => setActiveTab(tab)}>{tab[0].toUpperCase() + tab.slice(1)}{tab === "evidence" && <span>{selectedEvidence.length}</span>}</button>)}</nav><div className="g1-output-body">{activeTab === "output" && <StagePresenter stage={selected} semanticReview={selected.key === "understanding_semantic" ? { editable: run?.status === "AWAITING_SEMANTIC_REVIEW" && canOperate, contract: semanticDraft, busy, onColumnChange: updateSemanticColumn, onConfirm: () => void confirmSemantic() } : undefined}/>} {activeTab === "evidence" && <EvidenceOverview evidence={selectedEvidence} />}{activeTab === "activity" && (selected.nodes.length ? <div className="g1-activity-list">{selected.nodes.map((node) => <article className={node.status === "FAILED" ? "failed" : ""} key={node.node_key}><div><span>{node.node_key}</span><strong>{human(node.status)}</strong></div><div><span>STARTED</span><strong>{node.started_at ? new Date(node.started_at).toLocaleString() : "—"}</strong></div><div><span>COMPLETED</span><strong>{node.completed_at ? new Date(node.completed_at).toLocaleString() : "—"}</strong></div>{node.error && <p>{node.error}</p>}</article>)}</div> : <div className="g1-empty"><strong>No node activity yet</strong></div>)}</div>{reviewPanel}<footer className="g1-output-footer"><span>Output persisted by backend · {selected.nodes.length} canonical node{selected.nodes.length === 1 ? "" : "s"}</span><strong>{stageDuration(selected)}</strong></footer></> : <div className="g1-empty"><strong>No persisted node output is available yet</strong></div>}</section>
    </div></div>
  </aside>;
}
