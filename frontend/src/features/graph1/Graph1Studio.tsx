import { ChangeEvent, DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isMockMode } from "../../api";
import { apiBaseUrl } from "../../api/client";
import type { Dataset, Graph1NodeExecution, Graph1RuleDecision, Graph1Run, Job } from "../../types";
import "./graph1-studio.css";

const META: Record<string, [string, string]> = {
  raw_profiler: ["Raw profiler", "Profile thật của dataset đã upload."], profiler_digest: ["Profiler digest", "Tín hiệu chất lượng chuẩn hóa cho agent."],
  data_dictionary_generator: ["Data dictionary", "Từ điển dữ liệu được LLM suy luận."], dataset_understanding: ["Dataset understanding", "Semantic Contract từ profile và dictionary."],
  hitl_semantic_gate: ["Semantic review", "Steward xác nhận ngữ nghĩa."], rule_candidate_builder: ["Rule candidates", "Ứng viên rule deterministic có evidence."],
  prompt_customizer: ["Prompt customizer", "Prompt chuyên biệt theo dataset."], rule_proposer: ["Rule proposer", "Rule do LLM đề xuất."], hitl_gate: ["Rule approval", "Steward duyệt rules cuối."],
};
const STOP = new Set(["COMPLETED", "FAILED", "AWAITING_SEMANTIC_REVIEW", "AWAITING_RULE_REVIEW"]);
const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));
const pretty = (value: unknown) => JSON.stringify(value ?? {}, null, 2);
const human = (value: string) => value.replaceAll("_", " ");

const asRecord = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const asRecords = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item))) : [];
const show = (value: unknown, fallback = "—") => value === undefined || value === null || value === "" ? fallback : typeof value === "object" ? pretty(value) : String(value);
const pick = (source: Record<string, unknown>, keys: string[]) => keys.map((key) => source[key]).find((value) => value !== undefined && value !== null);
const nodeDuration = (node: Graph1NodeExecution) => {
  if (!node.started_at) return "—";
  const end = node.completed_at ? new Date(node.completed_at).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - new Date(node.started_at).getTime()) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
};

function DataTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return null;
  const columns = Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 8);
  return <div className="g1-table-wrap"><table className="g1-table"><thead><tr>{columns.map((key) => <th key={key}>{human(key)}</th>)}</tr></thead><tbody>{rows.slice(0, 20).map((row, index) => <tr key={index}>{columns.map((key) => <td key={key}>{show(row[key])}</td>)}</tr>)}</tbody></table></div>;
}

function RuleCards({ rules }: { rules: Record<string, unknown>[] }) {
  return <div className="g1-proposal-list">{rules.map((rule, index) => {
    const confidence = pick(rule, ["confidence", "confidence_score", "score"]);
    const evidence = pick(rule, ["selected_evidence_refs", "evidence_refs", "evidence"]);
    return <article className="g1-proposal-card" key={show(pick(rule, ["rule_id", "id"]), String(index))}><div className="g1-proposal-main"><span>{show(pick(rule, ["rule_id", "id"]), `RULE-${index + 1}`)}</span><strong>{show(pick(rule, ["rule_name", "name", "title", "rule_description"]), "Rule proposal")}</strong><p>{show(pick(rule, ["rule_description", "description", "ai_reasoning", "reasoning"]), "No description returned.")}</p><code>{show(pick(rule, ["expression", "condition", "parameters", "rule_type"]))}</code>{evidence !== undefined && <small>Evidence: {show(evidence)}</small>}</div>{confidence !== undefined && <strong className="g1-confidence">{typeof confidence === "number" && confidence <= 1 ? Math.round(confidence * 100) : show(confidence)}%</strong>}</article>;
  })}</div>;
}

function NodePresenter({ node }: { node: Graph1NodeExecution }) {
  const output = asRecord(node.output);
  if (!Object.keys(output).length) return <div className="g1-empty"><strong>Chưa có output</strong><span>Node sẽ cập nhật khi backend thực thi.</span></div>;
  if (node.node_key === "raw_profiler") {
    const profile = asRecord(output.dataset_profile); const source = asRecord(Object.values(profile)[0] ?? profile); const metadata = asRecord(pick(source, ["table_metadata", "metadata"])); const columns = asRecords(pick(source, ["columns", "column_profiles"])); const quality = asRecord(source.quality_summary);
    return <div className="g1-presenter"><div className="g1-metric-grid"><div><span>ROWS</span><strong>{show(pick(metadata, ["total_rows", "row_count"]))}</strong></div><div><span>COLUMNS</span><strong>{columns.length || show(pick(metadata, ["column_count", "total_columns"]))}</strong></div><div><span>COMPLETENESS</span><strong>{show(pick(quality, ["completeness", "completeness_score"]))}</strong></div><div><span>DUPLICATES</span><strong>{show(pick(quality, ["duplicate_rows", "duplicate_rate"]))}</strong></div></div><DataTable rows={columns}/></div>;
  }
  if (node.node_key === "profiler_digest") {
    const digest = asRecord(output.dataset_profile_digest); const source = asRecord(Object.values(digest)[0] ?? digest); const rows = asRecords(pick(source, ["columns", "column_signals", "fields"]));
    return <div className="g1-presenter"><p className="g1-summary">{show(pick(source, ["summary", "dataset_summary", "description"]), "Profiler digest generated by backend.")}</p><DataTable rows={rows}/><details><summary>Technical output</summary><pre>{pretty(digest)}</pre></details></div>;
  }
  if (node.node_key === "data_dictionary_generator") {
    const dictionary = output.normalized_data_dictionary ?? output.data_dictionary; const dictionaryRecord = asRecord(dictionary); const rows = asRecords(pick(dictionaryRecord, ["columns", "fields", "entries"]));
    return <div className="g1-presenter"><p className="g1-summary">Source: {show(output.data_dictionary_source)}</p>{rows.length ? <DataTable rows={rows}/> : <pre>{pretty(dictionary)}</pre>}</div>;
  }
  if (node.node_key === "dataset_understanding") {
    const contract = asRecord(output.semantic_contract); const fields = asRecords(pick(contract, ["columns", "fields", "entities"]));
    return <div className="g1-presenter"><div className="g1-understanding-hero"><span>SEMANTIC CONTRACT</span><h3>{show(pick(contract, ["dataset_name", "name", "title"]), "Dataset understanding")}</h3><p>{show(pick(contract, ["purpose", "description", "business_context"]))}</p></div><div className="g1-fact-grid"><div><span>GRAIN</span><strong>{show(pick(contract, ["grain", "row_grain"]))}</strong></div><div><span>PRIMARY KEY</span><strong>{show(pick(contract, ["primary_key", "primary_keys"]))}</strong></div></div><DataTable rows={fields}/><details><summary>Full contract</summary><pre>{pretty(contract)}</pre></details></div>;
  }
  if (node.node_key === "rule_candidate_builder") return <RuleCards rules={asRecords(output.rule_candidates)}/>;
  if (node.node_key === "prompt_customizer") { const prompts = asRecord(output.specialized_system_prompts ?? output.customized_prompts); return <div className="g1-prompt-list">{Object.entries(prompts).map(([key, value]) => <section className="g1-prompt-block" key={key}><strong>{human(key)}</strong><pre>{show(value)}</pre></section>)}</div>; }
  if (node.node_key === "rule_proposer") return <RuleCards rules={asRecords(output.proposed_rules)}/>;
  return <OutputView node={node}/>;
}

function collectEvidence(value: unknown, path = "output"): { path: string; value: unknown }[] {
  if (!value || typeof value !== "object") return [];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => key.toLowerCase().includes("evidence") ? [{ path: `${path}.${key}`, value: child }] : collectEvidence(child, `${path}.${key}`));
}

function OutputView({ node }: { node: Graph1NodeExecution }) {
  const entries = Object.entries(node.output ?? {});
  if (!entries.length) return <div className="g1-empty"><strong>Chưa có output</strong><span>Node sẽ cập nhật khi backend thực thi.</span></div>;
  return <div className="g1-real-output">{entries.map(([key, value]) => <section className="g1-json-section" key={key}><div><span>{human(key)}</span><small>{Array.isArray(value) ? `${value.length} items` : typeof value}</small></div>{typeof value === "string" && value.length < 240 ? <p>{value}</p> : <pre>{pretty(value)}</pre>}</section>)}</div>;
}

export function Graph1Studio({ onExit, onDatasetImported, initialDataset }: { onExit: () => void; onDatasetImported?: () => void; initialDataset?: Dataset | null }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [dataset, setDataset] = useState<Dataset | null>(initialDataset ?? null);
  const [run, setRun] = useState<Graph1Run | null>(null);
  const [nodes, setNodes] = useState<Graph1NodeExecution[]>([]);
  const [selectedKey, setSelectedKey] = useState("raw_profiler");
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [semanticText, setSemanticText] = useState("");
  const [decisions, setDecisions] = useState<Record<string, Graph1RuleDecision["action"]>>({});
  const [ruleEdits, setRuleEdits] = useState<Record<string, string>>({});
  const [activeTab, setActiveTab] = useState<"output" | "evidence" | "activity">("output");
  const autoStartRef = useRef(false);

  const refresh = useCallback(async (id: string) => {
    const [nextRun, nextNodes] = await Promise.all([api.getGraph1Run(id), api.listGraph1Nodes(id)]);
    setRun(nextRun); setNodes(nextNodes);
    const active = nextNodes.find((node) => ["RUNNING", "WAITING_REVIEW", "FAILED"].includes(node.status));
    if (active) setSelectedKey(active.node_key);
  }, []);

  useEffect(() => {
    if (!initialDataset || isMockMode || autoStartRef.current) return;
    autoStartRef.current = true;
    setDataset(initialDataset);
    const storedRun = sessionStorage.getItem("ridepulse.graph1.run");
    const storedDataset = sessionStorage.getItem("ridepulse.graph1.dataset");
    const start = async () => {
      setBusy(true); setError(""); setMessage("Đang khởi tạo canonical Graph 1…");
      try {
        if (storedRun && storedDataset === initialDataset.id) await refresh(storedRun);
        else {
          const created = await api.createGraph1Run(initialDataset.id);
          sessionStorage.setItem("ridepulse.graph1.run", created.id);
          sessionStorage.setItem("ridepulse.graph1.dataset", initialDataset.id);
          setRun(created); await refresh(created.id);
        }
        setMessage("");
      } catch (reason) {
        sessionStorage.removeItem("ridepulse.graph1.run"); sessionStorage.removeItem("ridepulse.graph1.dataset");
        setError(reason instanceof Error ? reason.message : "Không thể bắt đầu Graph 1."); setMessage("");
      } finally { setBusy(false); }
    };
    void start();
  }, [initialDataset, refresh]);
  useEffect(() => {
    if (!run || STOP.has(run.status) || !apiBaseUrl) return;
    const source = new EventSource(`${apiBaseUrl}/api/v1/graph1-runs/${encodeURIComponent(run.id)}/stream`, { withCredentials: true });
    source.addEventListener("snapshot", (event) => { const data = JSON.parse((event as MessageEvent).data) as { run: Graph1Run; nodes: Graph1NodeExecution[] }; setRun(data.run); setNodes(data.nodes); const active = data.nodes.find((node) => ["RUNNING", "WAITING_REVIEW", "FAILED"].includes(node.status)); if (active) setSelectedKey(active.node_key); });
    source.onerror = () => { source.close(); void refresh(run.id); };
    return () => source.close();
  }, [run?.id, run?.status, refresh]);
  useEffect(() => { if (!run || STOP.has(run.status)) return; const timer = window.setInterval(() => void refresh(run.id), 2500); return () => window.clearInterval(timer); }, [run?.id, run?.status, refresh]);

  const semanticNode = nodes.find((node) => node.node_key === "dataset_understanding");
  useEffect(() => { if (run?.status === "AWAITING_SEMANTIC_REVIEW" && !semanticText && semanticNode?.output.semantic_contract) setSemanticText(pretty(semanticNode.output.semantic_contract)); }, [run?.status, semanticNode, semanticText]);
  const rules = useMemo(() => { const value = nodes.find((node) => node.node_key === "rule_proposer")?.output.proposed_rules; return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object")) : []; }, [nodes]);
  useEffect(() => { if (rules.length && !Object.keys(decisions).length) { setDecisions(Object.fromEntries(rules.map((rule, index) => [String(rule.rule_id ?? `rule-${index}`), "approve"]))); setRuleEdits(Object.fromEntries(rules.map((rule, index) => { const id = String(rule.rule_id ?? `rule-${index}`); return [id, pretty({ type: rule.rule_type, column: rule.column, ...(typeof rule.parameters === "object" ? rule.parameters : {}) })]; }))); } }, [rules, decisions]);

  const upload = async (file: File) => {
    setError("");
    if (!/\.(csv|parquet)$/i.test(file.name)) return setError("Chỉ chấp nhận CSV hoặc Parquet.");
    if (file.size > 100 * 1024 * 1024) return setError("File vượt quá 100 MB.");
    if (isMockMode) return setError("Graph 1 cần backend thật. Đặt VITE_USE_MOCK_API=false.");
    setBusy(true);
    try {
      setMessage("Đang upload dataset…"); const imported = await api.importDataset(file); setDataset(imported.dataset); onDatasetImported?.();
      let job: Job = await api.getJob(imported.job.job_id); setMessage("Đang profile dữ liệu thật…");
      while (!["SUCCEEDED", "FAILED"].includes(job.status)) { await wait(700); job = await api.getJob(imported.job.job_id); setMessage(job.message || `Profiling ${Math.round(job.progress)}%`); }
      if (job.status === "FAILED") throw new Error(job.error || "Dataset profiling thất bại.");
      setMessage("Đang khởi tạo canonical Graph 1…"); const created = await api.createGraph1Run(imported.dataset.id); sessionStorage.setItem("ridepulse.graph1.run", created.id); sessionStorage.setItem("ridepulse.graph1.dataset", imported.dataset.id); setRun(created); await refresh(created.id); setMessage("");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể bắt đầu Graph 1."); setMessage(""); } finally { setBusy(false); }
  };
  const confirmSemantic = async () => { if (!run) return; setBusy(true); setError(""); try { const next = await api.confirmGraph1Semantic(run.id, JSON.parse(semanticText)); setRun(next); setSelectedKey("rule_candidate_builder"); await refresh(run.id); } catch (reason) { setError(reason instanceof Error ? reason.message : "Semantic Contract không hợp lệ."); } finally { setBusy(false); } };
  const confirmRules = async () => { if (!run) return; setBusy(true); setError(""); try { const payload = rules.map((rule, index) => { const id = String(rule.rule_id ?? `rule-${index}`); const action = decisions[id] ?? "reject"; return { rule_id: id, action, ...(action === "edit" ? { rule: JSON.parse(ruleEdits[id]) } : {}) } as Graph1RuleDecision; }); const next = await api.reviewGraph1Rules(run.id, payload); setRun(next); await refresh(run.id); } catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể lưu quyết định rule."); } finally { setBusy(false); } };
  const reset = () => { sessionStorage.removeItem("ridepulse.graph1.run"); sessionStorage.removeItem("ridepulse.graph1.dataset"); setRun(null); setNodes([]); setDataset(null); setError(""); setSemanticText(""); setDecisions({}); setRuleEdits({}); onExit(); };
  const selected = nodes.find((node) => node.node_key === selectedKey);
  useEffect(() => setActiveTab("output"), [selectedKey]);
  const selectedEvidence = selected ? collectEvidence(selected.output) : [];
  const done = nodes.filter((node) => ["SUCCEEDED", "SKIPPED"].includes(node.status)).length;
  const onFile = (event: ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (file) void upload(file); };
  const onDrop = (event: DragEvent<HTMLDivElement>) => { event.preventDefault(); setDragging(false); const file = event.dataTransfer.files[0]; if (file) void upload(file); };

  return <main className="g1-studio" id="main-content">
    <header className="g1-hero"><div><div className="g1-breadcrumb"><button type="button" onClick={onExit}>Workspace</button><span>›</span><span>Graph 1</span></div><div className="g1-title-row"><div className="g1-title-icon">G1</div><div><span className="eyebrow">CANONICAL AGENT WORKFLOW</span><h1>Agent execution studio</h1><p>Upload dữ liệu và theo dõi output thật của từng node.</p></div></div></div><span className={`g1-backend-badge ${isMockMode ? "offline" : "online"}`}><span/>{isMockMode ? "MOCK DISABLED" : "REAL BACKEND"}</span></header>
    {!run && !initialDataset && <section className="g1-upload-shell"><div className={`g1-dropzone ${dragging ? "dragging" : ""}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={onDrop}><input ref={fileInput} type="file" accept=".csv,.parquet" hidden onChange={onFile}/><div className="g1-upload-icon">↑</div><span className="eyebrow">DATASET INPUT</span><h2>Upload dataset để chạy Graph 1</h2><p>FastAPI sẽ profile file và chạy đủ canonical nodes.</p><button type="button" className="button primary" disabled={busy || isMockMode} onClick={() => fileInput.current?.click()}>{busy ? "Đang xử lý…" : "Chọn CSV hoặc Parquet"}</button><small>Tối đa 100 MB · raw rows không gửi tới LLM</small></div>{message && <div className="g1-operation" role="status"><span className="spinner"/><div><strong>{message}</strong><span>{dataset?.source_label ?? "Đang chuẩn bị"}</span></div></div>}</section>}
    {!run && initialDataset && <section className="g1-upload-shell"><div className="g1-operation g1-operation-centered" role="status"><span className="spinner"/><div><strong>{message || "Đang mở Agent Workflow…"}</strong><span>{initialDataset.source_label}</span></div></div></section>}
    {error && <div className="g1-error" role="alert"><strong>Graph 1 không thể tiếp tục</strong><span>{error}</span><button type="button" onClick={() => setError("")}>×</button></div>}
    {run && <><section className="g1-runbar"><div><span className="g1-live-dot"/><div><strong>{run.id}</strong><span>{dataset?.source_label ?? run.dataset_id}</span></div></div><div className="g1-run-meta"><span><small>STATUS</small><strong>{human(run.status)}</strong></span><span><small>NODES</small><strong>{done} / 9</strong></span><span><small>CURRENT</small><strong>{run.current_node ?? "QUEUED"}</strong></span><span><small>OWNER</small><strong>{run.created_by}</strong></span></div><div className="g1-progress"><span style={{ width: `${done / 9 * 100}%` }}/></div></section>
      <div className="g1-workspace">
        <aside className="g1-node-rail" aria-label="Graph 1 nodes"><div className="g1-rail-heading"><div><span>EXECUTION PATH</span><strong>9 canonical nodes</strong></div></div><ol>{nodes.map((node) => <li key={node.node_key} className={node.status.toLowerCase()}><button type="button" className={selectedKey === node.node_key ? "active" : ""} onClick={() => setSelectedKey(node.node_key)}><span className="g1-node-state">{node.status === "SUCCEEDED" ? "✓" : node.position}</span><span className="g1-node-copy"><span>{String(node.position).padStart(2, "0")} · {node.node_key}</span><strong>{META[node.node_key]?.[0]}</strong><small>{human(node.status)}</small></span><span className="g1-node-chevron">›</span></button></li>)}</ol></aside>
        <section className="g1-output" aria-live="polite">{selected ? <>
          <header className="g1-output-header"><div className="g1-output-title"><div className="g1-icon-tile">{selected.position}</div><div><span>NODE {String(selected.position).padStart(2, "0")} OUTPUT</span><h2>{META[selected.node_key]?.[0]}</h2><p>{META[selected.node_key]?.[1]}</p></div></div><div className="g1-output-status"><span className={`g1-chip ${selected.status === "SUCCEEDED" ? "success" : selected.status === "FAILED" ? "danger" : "warning"}`}>{human(selected.status)}</span><small>{nodeDuration(selected)}</small></div></header>
          <nav className="g1-output-tabs" aria-label="Node output views"><button type="button" className={activeTab === "output" ? "active" : ""} onClick={() => setActiveTab("output")}>Output</button><button type="button" className={activeTab === "evidence" ? "active" : ""} onClick={() => setActiveTab("evidence")}>Evidence <span>{selectedEvidence.length}</span></button><button type="button" className={activeTab === "activity" ? "active" : ""} onClick={() => setActiveTab("activity")}>Activity</button></nav>
          <div className="g1-output-body">{activeTab === "output" && <NodePresenter node={selected}/>} {activeTab === "evidence" && (selectedEvidence.length ? <div className="g1-evidence-list">{selectedEvidence.map((item) => <article key={item.path}><strong>{item.path}</strong><pre>{pretty(item.value)}</pre></article>)}</div> : <div className="g1-empty"><strong>No evidence returned</strong><span>This node output has no evidence fields.</span></div>)} {activeTab === "activity" && <div className="g1-activity-list"><article><span>STATUS</span><strong>{human(selected.status)}</strong></article><article><span>STARTED</span><strong>{selected.started_at ? new Date(selected.started_at).toLocaleString() : "—"}</strong></article><article><span>COMPLETED</span><strong>{selected.completed_at ? new Date(selected.completed_at).toLocaleString() : "—"}</strong></article>{selected.error && <article className="failed"><span>ERROR</span><strong>{selected.error}</strong></article>}</div>}</div>
          <footer className="g1-output-footer"><span>Output persisted by backend</span><strong>{nodeDuration(selected)}</strong></footer>
        </> : <div className="g1-empty"><strong>Đang khởi tạo nodes</strong></div>}</section>
      </div>
      {run.status === "AWAITING_SEMANTIC_REVIEW" && <section className="g1-review-panel"><div><span className="eyebrow">HITL SEMANTIC GATE</span><h2>Xác nhận Semantic Contract</h2><p>Graph chỉ tiếp tục sau khi steward xác nhận.</p></div><label htmlFor="semantic-contract">Semantic Contract JSON</label><textarea id="semantic-contract" value={semanticText} onChange={(event) => setSemanticText(event.target.value)} rows={16} spellCheck={false}/><button type="button" className="button primary" disabled={busy || !semanticText} onClick={() => void confirmSemantic()}>Xác nhận và tiếp tục</button></section>}
      {run.status === "AWAITING_RULE_REVIEW" && <section className="g1-review-panel"><div><span className="eyebrow">FINAL HITL GATE</span><h2>Duyệt rule proposals</h2><p>Mọi rule phải có quyết định và ít nhất một rule được duyệt.</p></div><div className="g1-review-rules">{rules.map((rule, index) => { const id = String(rule.rule_id ?? `rule-${index}`); return <article key={id}><div><span>{id}</span><strong>{String(rule.rule_name ?? rule.rule_description ?? "Rule proposal")}</strong><p>{String(rule.ai_reasoning ?? "Không có reasoning.")}</p>{decisions[id] === "edit" && <textarea aria-label={`Edited rule JSON for ${id}`} value={ruleEdits[id] ?? "{}"} onChange={(event) => setRuleEdits((current) => ({ ...current, [id]: event.target.value }))} rows={7} spellCheck={false}/>}</div><select aria-label={`Decision for ${id}`} value={decisions[id] ?? "approve"} onChange={(event) => setDecisions((current) => ({ ...current, [id]: event.target.value as Graph1RuleDecision["action"] }))}><option value="approve">Approve</option><option value="edit">Edit & approve</option><option value="reject">Reject</option></select></article>; })}</div><button type="button" className="button primary" disabled={busy || !rules.length} onClick={() => void confirmRules()}>Lưu quyết định và hoàn tất</button></section>}
      {run.status === "FAILED" && <div className="g1-terminal failed"><strong>Graph 1 failed</strong><p>{run.error}</p><button type="button" className="button secondary" onClick={reset}>Upload dataset khác</button></div>}{run.status === "COMPLETED" && <div className="g1-terminal completed"><strong>Graph 1 đã hoàn thành</strong><p>Rules đã được lưu thật vào backend.</p><button type="button" className="button secondary" onClick={reset}>Chạy dataset khác</button></div>}
    </>}
  </main>;
}
