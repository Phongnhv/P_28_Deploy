import { ChangeEvent, DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isMockMode } from "../../api";
import { apiBaseUrl } from "../../api/client";
import type { Dataset, Graph1NodeExecution, Graph1RuleDecision, Graph1Run, Job } from "../../types";
import { buildDisplayStages, EvidenceOverview, nodeKeyToStageKey, stageDuration, stageEvidence, StagePresenter } from "./presenters";
import "./graph1-studio.css";

const STOP = new Set(["COMPLETED", "FAILED", "AWAITING_SEMANTIC_REVIEW", "AWAITING_RULE_REVIEW"]);
type RuleDecisionState = Graph1RuleDecision["action"] | "undecided";
const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
const pretty = (value: unknown) => JSON.stringify(value ?? {}, null, 2);
const human = (value: string) => value.replaceAll("_", " ");

const asRecord = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

export function Graph1Studio({ onExit, onDatasetImported, onAnalyze, onRerun, onRunChange, initialDataset }: { onExit: () => void; onDatasetImported?: () => void; onAnalyze: (graph1RunId: string) => Promise<void>; onRerun?: () => void; onRunChange?: (run: Graph1Run | null) => void; initialDataset?: Dataset | null }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dataset, setDataset] = useState<Dataset | null>(initialDataset ?? null);
  const [run, setRun] = useState<Graph1Run | null>(null);
  const [nodes, setNodes] = useState<Graph1NodeExecution[]>([]);
  const [selectedKey, setSelectedKey] = useState("profile_info");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [semanticText, setSemanticText] = useState("");
  const [decisions, setDecisions] = useState<Record<string, RuleDecisionState>>({});
  const [ruleEdits, setRuleEdits] = useState<Record<string, Record<string, string>>>({});
  const [reviewValidation, setReviewValidation] = useState("");
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [activeTab, setActiveTab] = useState<"output" | "evidence" | "activity">("output");
  const autoStartRef = useRef(false);
  const reviewRunRef = useRef("");

  const refresh = useCallback(async (id: string) => {
    const [nextRun, nextNodes] = await Promise.all([api.getGraph1Run(id), api.listGraph1Nodes(id)]);
    setRun(nextRun); setNodes(nextNodes);
    const active = nextNodes.find((node) => ["RUNNING", "WAITING_REVIEW", "FAILED"].includes(node.status));
    const savedStage = sessionStorage.getItem(`ridepulse.graph1.stage.${id}`);
    if (savedStage) setSelectedKey(savedStage);
    else if (active) setSelectedKey(nodeKeyToStageKey(active.node_key));
  }, []);

  useEffect(() => { if (initialDataset) setDataset(initialDataset); }, [initialDataset]);
  useEffect(() => { onRunChange?.(run); }, [onRunChange, run]);
  useEffect(() => {
    if (!dataset || isMockMode || run || autoStartRef.current) return;
    const storedRun = sessionStorage.getItem("ridepulse.graph1.run");
    const storedDataset = sessionStorage.getItem("ridepulse.graph1.dataset");
    if (!storedRun || storedDataset !== dataset.id) return;
    autoStartRef.current = true;
    setBusy(true);
    setMessage("Loading the saved profiler run…");
    void refresh(storedRun)
      .then(() => setMessage(""))
      .catch(() => {
        if (sessionStorage.getItem("ridepulse.graph1.run") === storedRun) {
          sessionStorage.removeItem("ridepulse.graph1.run");
          sessionStorage.removeItem("ridepulse.graph1.dataset");
        }
        setMessage("");
      })
      .finally(() => {
        setBusy(false);
        autoStartRef.current = false;
      });
  }, [dataset, refresh, run]);
  const start = async (forceNew = false) => {
    if (!dataset || isMockMode || autoStartRef.current) return;
    autoStartRef.current = true;
    const storedRun = forceNew ? null : sessionStorage.getItem("ridepulse.graph1.run");
    const storedDataset = forceNew ? null : sessionStorage.getItem("ridepulse.graph1.dataset");
      setBusy(true); setError(""); setMessage("Đang khởi tạo profiler…");
      try {
        if (storedRun && storedDataset === dataset.id) await refresh(storedRun);
        else {
          const created = await api.createGraph1Run(dataset.id, dataset.dataset_version_id, dataset.profile_run_id);
          sessionStorage.setItem("ridepulse.graph1.run", created.id);
          sessionStorage.setItem("ridepulse.graph1.dataset", dataset.id);
          setRun(created); await refresh(created.id);
        }
        setMessage("");
      } catch (reason) {
        sessionStorage.removeItem("ridepulse.graph1.run"); sessionStorage.removeItem("ridepulse.graph1.dataset");
        setError(reason instanceof Error ? reason.message : "Không thể bắt đầu profiler."); setMessage("");
      } finally { setBusy(false); autoStartRef.current = false; }
  };
  useEffect(() => {
    if (!run || STOP.has(run.status) || !apiBaseUrl) return;
    const source = new EventSource(`${apiBaseUrl}/api/v1/graph1-runs/${encodeURIComponent(run.id)}/stream`, { withCredentials: true });
    source.addEventListener("snapshot", (event) => { const data = JSON.parse((event as MessageEvent).data) as { run: Graph1Run; nodes: Graph1NodeExecution[] }; setRun(data.run); setNodes(data.nodes); const savedStage = sessionStorage.getItem(`ridepulse.graph1.stage.${data.run.id}`); const active = data.nodes.find((node) => ["RUNNING", "WAITING_REVIEW", "FAILED"].includes(node.status)); if (savedStage) setSelectedKey(savedStage); else if (active) setSelectedKey(nodeKeyToStageKey(active.node_key)); });
    source.onerror = () => { source.close(); void refresh(run.id); };
    return () => source.close();
  }, [run?.id, run?.status, refresh]);
  useEffect(() => { if (!run || STOP.has(run.status)) return; const timer = window.setInterval(() => void refresh(run.id), 2500); return () => window.clearInterval(timer); }, [run?.id, run?.status, refresh]);
  useEffect(() => { if (run) sessionStorage.setItem(`ridepulse.graph1.stage.${run.id}`, selectedKey); }, [run?.id, selectedKey]);

  const semanticNode = nodes.find((node) => node.node_key === "dataset_understanding");
  useEffect(() => {
    if (run?.status !== "AWAITING_SEMANTIC_REVIEW" || semanticText) return;
    const savedDraft = sessionStorage.getItem(`ridepulse.graph1.semantic.${run.id}`);
    if (savedDraft) setSemanticText(savedDraft);
    else if (semanticNode?.output.semantic_contract) setSemanticText(pretty(semanticNode.output.semantic_contract));
  }, [run?.id, run?.status, semanticNode, semanticText]);
  useEffect(() => { if (run?.status === "AWAITING_SEMANTIC_REVIEW" && semanticText) sessionStorage.setItem(`ridepulse.graph1.semantic.${run.id}`, semanticText); }, [run?.id, run?.status, semanticText]);
  const rules = useMemo(() => { const value = nodes.find((node) => node.node_key === "rule_proposer")?.output.proposed_rules; return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")) : []; }, [nodes]);
  useEffect(() => {
    if (!run || !rules.length) return;
    if (reviewRunRef.current === run.id && Object.keys(decisions).length) return;
    reviewRunRef.current = run.id;
    const storageKey = `ridepulse.graph1.review.${run.id}`;
    try {
      const stored = JSON.parse(sessionStorage.getItem(storageKey) ?? "{}") as { decisions?: Record<string, RuleDecisionState>; edits?: Record<string, Record<string, string>> };
      if (stored.decisions && Object.keys(stored.decisions).length) {
        setDecisions(stored.decisions);
        setRuleEdits(stored.edits ?? {});
        return;
      }
    } catch { /* Ignore an invalid local draft and start clean. */ }
    setDecisions(Object.fromEntries(rules.map((rule, index) => [String(rule.rule_id ?? `rule-${index}`), "undecided"])));
    setRuleEdits(Object.fromEntries(rules.map((rule, index) => {
      const id = String(rule.rule_id ?? `rule-${index}`);
      const parameters = rule.parameters && typeof rule.parameters === "object" && !Array.isArray(rule.parameters) ? rule.parameters as Record<string, unknown> : {};
      return [id, { type: String(rule.rule_type ?? ""), name: String(rule.rule_name ?? ""), description: String(rule.rule_description ?? ""), column: String(rule.column ?? ""), min_value: String(parameters.min_value ?? parameters.min ?? ""), max_value: String(parameters.max_value ?? parameters.max ?? ""), max_null_pct: String(parameters.max_null_pct ?? ""), accepted_values: Array.isArray(parameters.accepted_values) ? parameters.accepted_values.join(", ") : "", regex: String(parameters.regex ?? ""), target_column: String(parameters.target_column ?? ""), operator: String(parameters.operator ?? ""), min_row_count: String(parameters.min_row_count ?? "") }];
    })));
  }, [run, rules, decisions]);
  useEffect(() => {
    if (!run || !Object.keys(decisions).length) return;
    sessionStorage.setItem(`ridepulse.graph1.review.${run.id}`, JSON.stringify({ decisions, edits: ruleEdits }));
  }, [run, decisions, ruleEdits]);

  const upload = async (file: File) => {
    setError("");
    if (!/\.(csv|parquet)$/i.test(file.name)) return setError("Chỉ chấp nhận CSV hoặc Parquet.");
    if (file.size > 100 * 1024 * 1024) return setError("File vượt quá 100 MB.");
    if (isMockMode) return setError("Profiler cần backend thật. Đặt VITE_USE_MOCK_API=false.");
    setBusy(true);
    try {
      setMessage("Đang upload dataset…"); const imported = await api.importDataset(file); setDataset(imported.dataset); onDatasetImported?.();
      let job: Job = await api.getJob(imported.job.job_id); setMessage("Đang profile dữ liệu thật…");
      while (!["SUCCEEDED", "FAILED"].includes(job.status)) { await wait(700); job = await api.getJob(imported.job.job_id); setMessage(job.message || `Profiling ${Math.round(job.progress)}%`); }
      if (job.status === "FAILED") throw new Error(job.error || "Dataset profiling thất bại.");
      setMessage("Đang khởi tạo profiler…"); const created = await api.createGraph1Run(imported.dataset.id, imported.dataset.dataset_version_id, imported.dataset.profile_run_id); sessionStorage.setItem("ridepulse.graph1.run", created.id); sessionStorage.setItem("ridepulse.graph1.dataset", imported.dataset.id); setRun(created); await refresh(created.id); setMessage("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể bắt đầu profiler."); setMessage(""); } finally { setBusy(false); }
  };
  const confirmSemantic = async () => { if (!run) return; setBusy(true); setError(""); try { const next = await api.confirmGraph1Semantic(run.id, JSON.parse(semanticText)); sessionStorage.removeItem(`ridepulse.graph1.semantic.${run.id}`); sessionStorage.setItem(`ridepulse.graph1.stage.${run.id}`, "rule_candidate_builder"); setRun(next); setSelectedKey("rule_candidate_builder"); await refresh(run.id); } catch (reason) { setError(reason instanceof Error ? reason.message : "Semantic Contract không hợp lệ."); } finally { setBusy(false); } };
  const confirmRules = async () => {
    if (!run) return;
    const firstUndecided = rules.findIndex((rule, index) => decisions[String(rule.rule_id ?? `rule-${index}`)] === "undecided" || !decisions[String(rule.rule_id ?? `rule-${index}`)]);
    if (firstUndecided >= 0) {
      setReviewValidation(`Còn ${rules.filter((rule, index) => { const value = decisions[String(rule.rule_id ?? `rule-${index}`)]; return !value || value === "undecided"; }).length} rule chưa được quyết định.`);
      document.getElementById(`g1-review-rule-${firstUndecided}`)?.focus();
      return;
    }
    if (!Object.values(decisions).some((value) => value === "approve" || value === "edit")) {
      setReviewValidation("Cần approve hoặc edit & approve ít nhất một rule để hoàn tất profiler.");
      return;
    }
    setBusy(true); setError(""); setReviewValidation("");
    try {
      const payload = rules.map((rule, index) => {
        const id = String(rule.rule_id ?? `rule-${index}`);
        const action = decisions[id] as Graph1RuleDecision["action"];
        const draft = ruleEdits[id] ?? {};
        const numeric = (key: string) => draft[key] === "" || draft[key] === undefined ? undefined : Number(draft[key]);
        const editedRule = { type: draft.type, rule_name: draft.name, rule_description: draft.description, column: draft.column || null, parameters: { min_value: numeric("min_value"), max_value: numeric("max_value"), max_null_pct: numeric("max_null_pct"), accepted_values: draft.accepted_values ? draft.accepted_values.split(",").map((item) => item.trim()).filter(Boolean) : undefined, regex: draft.regex || undefined, target_column: draft.target_column || undefined, operator: draft.operator || undefined, min_row_count: numeric("min_row_count") } };
        return { rule_id: id, action, ...(action === "edit" ? { rule: editedRule } : {}) } as Graph1RuleDecision;
      });
      const next = await api.reviewGraph1Rules(run.id, payload); setRun(next); await refresh(run.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể lưu quyết định rule."); } finally { setBusy(false); }
  };
  const reset = () => { if (run) { sessionStorage.removeItem(`ridepulse.graph1.review.${run.id}`); sessionStorage.removeItem(`ridepulse.graph1.stage.${run.id}`); sessionStorage.removeItem(`ridepulse.graph1.semantic.${run.id}`); } sessionStorage.removeItem("ridepulse.graph1.run"); sessionStorage.removeItem("ridepulse.graph1.dataset"); setRun(null); setNodes([]); setDataset(null); setError(""); setSemanticText(""); setDecisions({}); setRuleEdits({}); setReviewValidation(""); reviewRunRef.current = ""; onExit(); };
  const analyze = async () => {
    if (!run || isMockMode) return;
    setAnalysisBusy(true); setError("");
    try { await onAnalyze(run.id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to start Rule Proposal and Anomaly Detection."); }
    finally { setAnalysisBusy(false); }
  };
  const stages = useMemo(() => buildDisplayStages(nodes), [nodes]);
  const selected = stages.find((stage) => stage.key === selectedKey) ?? stages[0];
  useEffect(() => setActiveTab("output"), [selectedKey]);
  const selectedEvidence = selected ? stageEvidence(selected) : [];
  const semanticDraft = useMemo(() => {
    try { return asRecord(JSON.parse(semanticText || "{}")); } catch { return {}; }
  }, [semanticText]);
  const updateSemanticColumn = (tableKey: string, index: number, field: string, value: string | boolean | number) => {
    const next = structuredClone(semanticDraft);
    const tables = asRecord(next.tables);
    const table = asRecord(tables[tableKey]);
    const columns = Array.isArray(table.columns) ? [...table.columns] as Record<string, unknown>[] : [];
    columns[index] = { ...asRecord(columns[index]), [field]: value };
    table.columns = columns; tables[tableKey] = table; next.tables = tables;
    setSemanticText(pretty(next));
  };
  const done = nodes.filter((node) => ["SUCCEEDED", "SKIPPED"].includes(node.status)).length;
  const approvedCount = Object.values(decisions).filter((value) => value === "approve").length;
  const editedCount = Object.values(decisions).filter((value) => value === "edit").length;
  const rejectedCount = Object.values(decisions).filter((value) => value === "reject").length;
  const pendingCount = rules.filter((rule, index) => { const value = decisions[String(rule.rule_id ?? `rule-${index}`)]; return !value || value === "undecided"; }).length;
  const decidedCount = Math.max(0, rules.length - pendingCount);
  const gateOutput = asRecord(nodes.find((node) => node.node_key === "hitl_gate")?.output);
  const finalApprovedCount = Number(gateOutput.approved_count ?? gateOutput.approved ?? approvedCount + editedCount);
  const approveAllRules = () => {
    setReviewValidation("");
    setDecisions(Object.fromEntries(rules.map((rule, index) => [String(rule.rule_id ?? `rule-${index}`), "approve"] as const)));
  };
  const onFile = (event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (file) void upload(file); };
  const onDrop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(false); const file = event.dataTransfer.files[0]; if (file) void upload(file); };

  return <main className="g1-studio" id="main-content">
    <header className="g1-hero"><div><div className="g1-breadcrumb"><button type="button" onClick={onExit}>Workspace</button><span>›</span><span>Profiler</span></div><div className="g1-title-row"><div className="g1-title-icon">P</div><div><span className="eyebrow">DATA PROFILER</span><h1>Profiler execution studio</h1><p>Upload dữ liệu và theo dõi output thật của từng node.</p></div></div></div><span className={`g1-backend-badge ${isMockMode ? "offline" : "online"}`}><span/>{isMockMode ? "MOCK DISABLED" : "REAL BACKEND"}</span></header>
    {!run && !initialDataset && <section className="g1-upload-shell"><div className={`g1-dropzone ${dragging ? "dragging" : ""}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}><input ref={fileInput} type="file" accept=".csv,.parquet" hidden onChange={onFile}/><div className="g1-upload-icon">↑</div><span className="eyebrow">DATASET INPUT</span><h2>Upload dataset để chạy profiler</h2><p>FastAPI sẽ profile file và chạy đủ canonical nodes.</p><button type="button" className="button primary" disabled={busy || isMockMode} onClick={() => fileInput.current?.click()}>{busy ? "Đang xử lý…" : "Chọn CSV hoặc Parquet"}</button><small>Tối đa 100 MB · raw rows không gửi tới LLM</small></div>{message && <div className="g1-operation" role="status"><span className="spinner"/><div><strong>{message}</strong><span>{dataset?.source_label ?? "Đang chuẩn bị"}</span></div></div>}</section>}
    {!run && dataset && <section className="g1-upload-shell"><div className="g1-operation g1-operation-centered"><div><strong>Ready to run profiler</strong><span>{dataset.source_label}</span></div><button type="button" className="button primary" disabled={busy || isMockMode} onClick={() => void start()}>{busy ? "Starting profiler…" : "Run Profiler →"}</button></div></section>}
    {error && <div className="g1-error" role="alert"><strong>Profiler không thể tiếp tục</strong><span>{error}</span><button type="button" onClick={() => setError("")}>×</button></div>}
    {run && <><section className="g1-runbar"><div><span className="g1-live-dot"/><div><strong>{run.id}</strong><span>{dataset?.source_label ?? run.dataset_id}</span></div></div><div className="g1-run-meta"><span><small>STATUS</small><strong>{human(run.status)}</strong></span><span><small>NODES</small><strong>{done} / 9</strong></span><span><small>CURRENT</small><strong>{run.current_node ?? "QUEUED"}</strong></span><span><small>OWNER</small><strong>{run.created_by}</strong></span></div><div className="g1-progress"><span style={{ width: `${done / 9 * 100}%` }}/></div></section>
      <div className="g1-workspace">
        <aside className="g1-node-rail" aria-label="Profiler display stages"><div className="g1-rail-heading"><div><span>EXECUTION PATH</span><strong>7 display stages · 9 backend nodes</strong></div></div><ol>{stages.map((stage) => <li key={stage.key} className={stage.status.toLowerCase()}><button type="button" className={selectedKey === stage.key ? "active" : ""} onClick={() => setSelectedKey(stage.key)}><span className="g1-node-state">{stage.status === "SUCCEEDED" ? "✓" : stage.canonicalLabel}</span><span className="g1-node-copy"><span>{stage.canonicalLabel} · {stage.key}</span><strong>{stage.title}</strong><small>{human(stage.status)}</small></span><span className="g1-node-chevron">›</span></button></li>)}</ol></aside>
        <section className="g1-output" aria-live="polite">{selected ? <>
          <header className="g1-output-header"><div className="g1-output-title"><div className="g1-icon-tile">{selected.canonicalLabel}</div><div><span>STAGE {selected.canonicalLabel} OUTPUT</span><h2>{selected.title}</h2><p>{selected.description}</p></div></div><div className="g1-output-status"><span className={`g1-chip ${selected.status === "SUCCEEDED" ? "success" : selected.status === "FAILED" ? "danger" : "warning"}`}>{human(selected.status)}</span><small>{stageDuration(selected)}</small></div></header>
          <nav className="g1-output-tabs" aria-label="Node output views"><button type="button" className={activeTab === "output" ? "active" : ""} onClick={() => setActiveTab("output")}>Output</button><button type="button" className={activeTab === "evidence" ? "active" : ""} onClick={() => setActiveTab("evidence")}>Evidence <span>{selectedEvidence.length}</span></button><button type="button" className={activeTab === "activity" ? "active" : ""} onClick={() => setActiveTab("activity")}>Activity</button></nav>
          <div className="g1-output-body">{activeTab === "output" && <StagePresenter stage={selected} semanticReview={selected.key === "understanding_semantic" ? { editable: run.status === "AWAITING_SEMANTIC_REVIEW", contract: semanticDraft, busy, onColumnChange: updateSemanticColumn, onConfirm: () => void confirmSemantic() } : undefined}/>} {activeTab === "evidence" && <EvidenceOverview evidence={selectedEvidence}/>} {activeTab === "activity" && <div className="g1-activity-list">{selected.nodes.map((node) => <article className={node.status === "FAILED" ? "failed" : ""} key={node.node_key}><div><span>{node.node_key}</span><strong>{human(node.status)}</strong></div><div><span>STARTED</span><strong>{node.started_at ? new Date(node.started_at).toLocaleString() : "—"}</strong></div><div><span>COMPLETED</span><strong>{node.completed_at ? new Date(node.completed_at).toLocaleString() : "—"}</strong></div>{node.error && <p>{node.error}</p>}</article>)}</div>}</div>
          <footer className="g1-output-footer"><span>Output persisted by backend · {selected.nodes.length} canonical node{selected.nodes.length > 1 ? "s" : ""}</span><strong>{stageDuration(selected)}</strong></footer>
        </> : <div className="g1-empty"><strong>Đang khởi tạo nodes</strong></div>}</section>
      </div>
      {run.status === "AWAITING_RULE_REVIEW" && <section className="g1-review-panel g1-final-review"><header className="g1-review-header"><div><span className="eyebrow">FINAL HITL GATE</span><h2>Duyệt rule proposals</h2><p>Đọc mục đích, điều kiện và evidence; quyết định từng rule trước khi hoàn tất.</p></div><div><span>REVIEW OWNER</span><strong>{run.created_by}</strong></div></header>
        <div className="g1-review-summary six"><span><small>TOTAL</small><strong>{rules.length}</strong></span><span><small>DECIDED</small><strong>{decidedCount}/{rules.length}</strong></span><span><small>APPROVED</small><strong>{approvedCount}</strong></span><span><small>EDITED</small><strong>{editedCount}</strong></span><span><small>REJECTED</small><strong>{rejectedCount}</strong></span><span><small>PENDING</small><strong>{pendingCount}</strong></span></div>
        {reviewValidation && <div className="g1-review-validation" role="alert"><strong>Chưa thể hoàn tất review</strong><span>{reviewValidation}</span></div>}
        <div className="g1-review-progress" aria-label={`${decidedCount} of ${rules.length} rules decided`}><div><span>Tiến độ quyết định</span><strong>{decidedCount}/{rules.length}</strong></div><progress max={Math.max(1, rules.length)} value={decidedCount}/><button type="button" className="button secondary" disabled={busy || !rules.length || approvedCount === rules.length} onClick={approveAllRules}>Approve all rules</button></div>
        <div className="g1-review-rules">{rules.map((rule, index) => {
          const id = String(rule.rule_id ?? `rule-${index}`);
          const draft = ruleEdits[id] ?? {};
          const parameters = asRecord(rule.parameters);
          const refs = Array.isArray(rule.selected_evidence_refs) ? rule.selected_evidence_refs.map(String) : [];
          const rawConfidence = typeof rule.confidence_score === "number" ? rule.confidence_score : asRecord(rule.confidence).overall;
          const confidence = typeof rawConfidence === "number" ? `${Math.round((rawConfidence <= 1 ? rawConfidence * 100 : rawConfidence) * 10) / 10}%` : "—";
          const updateDraft = (key: string, value: string) => setRuleEdits((current) => ({ ...current, [id]: { ...(current[id] ?? {}), [key]: value } }));
          return <article id={`g1-review-rule-${index}`} tabIndex={-1} className={`g1-review-rule ${decisions[id] ?? "undecided"}`} key={id}>
            <div className="g1-review-rule-head"><div className="g1-review-identity"><code>{id}</code><strong>{String(rule.rule_name ?? rule.rule_description ?? "Rule proposal")}</strong><p>{String(rule.rule_description ?? "Chưa có mô tả.")}</p></div><label className="g1-decision"><span>Decision</span><select aria-label={`Decision for ${id}`} value={decisions[id] ?? "undecided"} onChange={(event) => { setReviewValidation(""); setDecisions((current) => ({ ...current, [id]: event.target.value as RuleDecisionState })); }}><option value="undecided">Chưa quyết định</option><option value="approve">Approve</option><option value="edit">Edit & approve</option><option value="reject">Reject</option></select></label></div>
            <div className="g1-review-rule-grid"><section><span>TARGET</span><strong>{String(rule.table_name ?? "Table")}.{String(rule.column ?? "Table-level")}</strong></section><section><span>CONDITION</span><strong>{String(rule.rule_type ?? "—")}</strong><small>{Object.entries(parameters).filter(([, value]) => value !== null && value !== undefined && value !== "").map(([key, value]) => `${human(key)}: ${Array.isArray(value) ? value.join(", ") : String(value)}`).join(" · ") || "Không có tham số"}</small></section><section><span>RISK</span><strong>{String(rule.severity ?? "—")} · {String(rule.dimension ?? "—")}</strong></section><section><span>CONFIDENCE / EVIDENCE</span><strong>{confidence} · {refs.length} refs</strong><small>{refs.join(" · ") || "Không có evidence reference"}</small></section></div>
            <p className="g1-review-reasoning"><span>REASONING</span>{String(rule.ai_reasoning ?? rule.business_rationale ?? "Không có reasoning.")}</p>
            {decisions[id] === "edit" && <fieldset className="g1-rule-edit"><legend>Chỉnh sửa rule theo trường</legend><div className="g1-rule-edit-group"><h3>Định danh và mục đích</h3><label>Rule name<input value={draft.name ?? ""} onChange={(event) => updateDraft("name", event.target.value)} /></label><label>Rule type<input value={draft.type ?? ""} onChange={(event) => updateDraft("type", event.target.value)} required /></label><label className="wide">Description<textarea rows={2} value={draft.description ?? ""} onChange={(event) => updateDraft("description", event.target.value)} /></label><label>Column<input value={draft.column ?? ""} onChange={(event) => updateDraft("column", event.target.value)} /></label></div><div className="g1-rule-edit-group"><h3>Điều kiện và ngưỡng</h3><label>Min value<input type="number" value={draft.min_value ?? ""} onChange={(event) => updateDraft("min_value", event.target.value)} /></label><label>Max value<input type="number" value={draft.max_value ?? ""} onChange={(event) => updateDraft("max_value", event.target.value)} /></label><label>Max null %<input type="number" value={draft.max_null_pct ?? ""} onChange={(event) => updateDraft("max_null_pct", event.target.value)} /></label><label className="wide">Accepted values<input value={draft.accepted_values ?? ""} onChange={(event) => updateDraft("accepted_values", event.target.value)} /></label><label className="wide">Regex<input value={draft.regex ?? ""} onChange={(event) => updateDraft("regex", event.target.value)} /></label><label>Target column<input value={draft.target_column ?? ""} onChange={(event) => updateDraft("target_column", event.target.value)} /></label><label>Operator<input value={draft.operator ?? ""} onChange={(event) => updateDraft("operator", event.target.value)} /></label><label>Min row count<input type="number" value={draft.min_row_count ?? ""} onChange={(event) => updateDraft("min_row_count", event.target.value)} /></label></div></fieldset>}
          </article>;
        })}</div>
        <div className="g1-review-actions"><div><strong>{pendingCount ? `${pendingCount} rule đang chờ quyết định` : "Tất cả rule đã có quyết định"}</strong><span>Cần ít nhất một rule Approve hoặc Edit & approve.</span></div><button type="button" className="button primary" disabled={busy || !rules.length} onClick={() => void confirmRules()}>{busy ? "Đang lưu…" : "Lưu quyết định và hoàn tất"}</button></div>
      </section>}
      {run.status === "FAILED" && <div className="g1-terminal failed"><strong>Profiler failed</strong><p>{run.error}</p><button type="button" className="button secondary" onClick={reset}>Upload dataset khác</button></div>}{run.status === "COMPLETED" && <div className="g1-terminal completed"><div><strong>Profiler đã hoàn thành</strong><p>{finalApprovedCount} rule đã được duyệt và sẵn sàng cho Rule Proposal.</p></div><div className="g1-terminal-actions"><button type="button" className="button primary" disabled={analysisBusy || isMockMode || finalApprovedCount < 1} onClick={() => void analyze()}>{analysisBusy ? "Đang khởi tạo phân tích…" : "Run Rule Proposal & Anomaly Detection"}</button><button type="button" className="button secondary" disabled={analysisBusy} onClick={reset}>Chạy dataset khác</button></div>{isMockMode && <small>Analysis Studio yêu cầu real backend.</small>}</div>}
      {run && (run.status === "COMPLETED" || run.status === "FAILED") && <div className="g1-rerun-strip"><span>Giữ phiên profiler hiện tại trong lịch sử và tạo một phiên mới.</span><button type="button" className="button secondary" disabled={busy || analysisBusy || isMockMode} onClick={() => { onRerun?.(); void start(true); }}>Rerun Profiler</button></div>}
    </>}
  </main>;
}
