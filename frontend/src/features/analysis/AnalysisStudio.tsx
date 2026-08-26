import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Clock3,
  Copy,
  Database,
  Download,
  FileText,
  Search,
  ShieldAlert,
  XCircle,
} from "./icons";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api } from "../../api";
import { apiBaseUrl } from "../../api/client";
import type {
  AnalysisGraph2Result,
  AnalysisNodeExecution,
  AnalysisResult,
  AnalysisRun,
} from "../../types";
import "./analysis-studio.css";

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "FAILED"]);
const STATUS_COLORS: Record<string, string> = {
  PASS: "#10b981",
  FAIL: "#ef4444",
  ERROR: "#f97316",
  SKIPPED: "#94a3b8",
  RESULT_MISMATCH: "#a855f7",
};
const NODE_LABELS: Record<string, string> = {
  prepare_approved_rules: "Approved rules",
  test_generator: "Generate dbt tests",
  validate_dbt_project: "Validate dbt project",
  dbt_validation_failed: "Validation failure branch",
  test_runner: "Execute tests",
  persist_report: "Persist test report",
  anomaly_detector: "Detect anomaly signals",
  hypothesis_agent: "Generate hypotheses",
  persist_analysis: "Persist anomaly analysis",
  report_writer: "Write steward report",
};

function formatNumber(value: number) {
  return new Intl.NumberFormat("vi-VN").format(value || 0);
}

function formatPercent(value: number) {
  return `${((value || 0) * 100).toFixed(2)}%`;
}

function formatDuration(value?: number | null) {
  if (value === null || value === undefined) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

function displayAnalysisArea(value?: string | null) {
  const normalized = (value ?? "").replaceAll("_", " ").toUpperCase();
  if (normalized.includes("GRAPH 2") || normalized === "GRAPH2" || normalized === "GRAPH 2") return "RULE PROPOSAL";
  if (normalized.includes("GRAPH 3") || normalized === "GRAPH3" || normalized === "GRAPH 3") return "ANOMALY DETECTION";
  return value ?? "—";
}

function slugify(value: string) {
  return value
    .toLocaleLowerCase("vi")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

function statusIcon(status: string) {
  if (status === "SUCCEEDED" || status === "COMPLETED") return <CheckCircle2 aria-hidden="true" />;
  if (status === "FAILED" || status === "PARTIAL") return <AlertTriangle aria-hidden="true" />;
  if (status === "RUNNING") return <Activity aria-hidden="true" />;
  return <Clock3 aria-hidden="true" />;
}

function Metric({ label, value, tone = "neutral" }: { label: string; value: string | number; tone?: string }) {
  return <div className={`analysis-metric ${tone}`}><span>{label}</span><strong>{value}</strong></div>;
}

export function AnalysisStudio({
  analysisRunId,
  onExit,
  onBackToGraph1,
  onRerun,
  rerunBusy = false,
}: {
  analysisRunId: string;
  onExit: () => void;
  onBackToGraph1: () => void;
  onRerun?: () => void;
  rerunBusy?: boolean;
}) {
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [nodes, setNodes] = useState<AnalysisNodeExecution[]>([]);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [severityFilter, setSeverityFilter] = useState("ALL");
  const [dimensionFilter, setDimensionFilter] = useState("ALL");
  const [anomalyOnly, setAnomalyOnly] = useState(false);
  const [sortBy, setSortBy] = useState<"violation" | "failed">("violation");
  const [search, setSearch] = useState("");
  const [expandedRule, setExpandedRule] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [nextRun, nextNodes, nextResult] = await Promise.all([
        api.getAnalysisRun(analysisRunId),
        api.listAnalysisNodes(analysisRunId),
        api.getAnalysisResult(analysisRunId),
      ]);
      setRun(nextRun);
      setNodes(nextNodes);
      setResult(nextResult);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể tải phiên phân tích.");
    }
  }, [analysisRunId]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!run || TERMINAL.has(run.status) || !apiBaseUrl) return;
    const source = new EventSource(
      `${apiBaseUrl}/api/v1/analysis-runs/${encodeURIComponent(analysisRunId)}/stream`,
      { withCredentials: true },
    );
    source.addEventListener("snapshot", (event) => {
      const snapshot = JSON.parse((event as MessageEvent).data) as { run: AnalysisRun; nodes: AnalysisNodeExecution[] };
      setRun(snapshot.run);
      setNodes(snapshot.nodes);
      void api.getAnalysisResult(analysisRunId).then(setResult).catch(() => undefined);
    });
    source.onerror = () => { source.close(); void refresh(); };
    return () => source.close();
  }, [analysisRunId, refresh, run?.status]);
  useEffect(() => {
    if (!run || TERMINAL.has(run.status)) return;
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(timer);
  }, [refresh, run?.status]);

  const graph2 = result?.graph2;
  const graph3 = result?.graph3;
  const report = result?.report;
  const summary = graph2?.summary;
  const dimensions = useMemo(
    () => [...new Set((graph2?.results ?? []).map((row) => row.dimension))].sort(),
    [graph2?.results],
  );
  const severities = useMemo(
    () => [...new Set((graph2?.results ?? []).map((row) => row.severity))].sort(),
    [graph2?.results],
  );
  const filteredRows = useMemo(() => {
    const term = search.trim().toLocaleLowerCase("vi");
    return [...(graph2?.results ?? [])]
      .filter((row) => statusFilter === "ALL" || row.status === statusFilter)
      .filter((row) => severityFilter === "ALL" || row.severity === severityFilter)
      .filter((row) => dimensionFilter === "ALL" || row.dimension === dimensionFilter)
      .filter((row) => !anomalyOnly || row.anomaly.flagged)
      .filter((row) => !term || `${row.rule_id} ${row.rule_title} ${row.table_name} ${row.column ?? ""}`.toLocaleLowerCase("vi").includes(term))
      .sort((a, b) => sortBy === "failed" ? b.failed_count - a.failed_count : b.violation_rate - a.violation_rate);
  }, [anomalyOnly, dimensionFilter, graph2?.results, search, severityFilter, sortBy, statusFilter]);

  const statusData = useMemo(() => summary ? [
    { name: "PASS", value: summary.passed },
    { name: "FAIL", value: summary.failed },
    { name: "ERROR", value: summary.errors },
    { name: "SKIPPED", value: summary.skipped },
  ].filter((item) => item.value > 0) : [], [summary]);
  const violationData = useMemo(() => [...(graph2?.results ?? [])]
    .filter((row) => row.violation_rate > 0)
    .sort((a, b) => b.violation_rate - a.violation_rate)
    .slice(0, 10)
    .map((row) => ({ name: row.rule_id.replace(/^source_rows\./, ""), rate: Number((row.violation_rate * 100).toFixed(2)), anomaly: row.anomaly.flagged })), [graph2?.results]);
  const signalData = useMemo(() => (graph3?.signals ?? []).slice(0, 12).map((signal) => ({
    name: signal.target_id.replace(/^source_rows\./, ""),
    score: Number((signal.score * 100).toFixed(1)),
    family: signal.family,
  })), [graph3?.signals]);
  const toc = useMemo(() => (report?.markdown.match(/^##\s+.+$/gm) ?? []).map((line) => {
    const title = line.replace(/^##\s+/, "").trim();
    return { title, id: slugify(title) };
  }), [report?.markdown]);

  const copyReport = async () => {
    if (!report?.markdown) return;
    await navigator.clipboard.writeText(report.markdown);
    setStatusMessage("Đã sao chép báo cáo Markdown.");
  };
  const reportDownloadable = Boolean(
    report?.available && (!run?.dataset_version_id || report.artifact_status === "REGISTERED"),
  );
  const downloadReport = () => {
    if (!report?.markdown || !reportDownloadable) return;
    const url = URL.createObjectURL(new Blob([report.markdown], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = report.file_name || `steward-report-${analysisRunId}.md`;
    anchor.click();
    URL.revokeObjectURL(url);
    setStatusMessage("Đã tạo file báo cáo để tải xuống.");
  };

  const completedNodes = nodes.filter((node) => ["SUCCEEDED", "SKIPPED"].includes(node.status)).length;
  const progress = nodes.length ? Math.round(completedNodes / nodes.length * 100) : 0;

  return <main className="analysis-studio" id="main-content">
    <header className="analysis-hero">
      <div>
        <nav className="analysis-breadcrumb" aria-label="Breadcrumb">
          <button type="button" onClick={onExit}>Workspace</button><span>›</span>
          <button type="button" onClick={onBackToGraph1}>Profiler</button><span>›</span>
          <span>Analysis</span>
        </nav>
        <div className="analysis-title-row"><div className="analysis-title-icon"><Activity aria-hidden="true" /></div><div>
          <span className="eyebrow">RULE PROPOSAL + ANOMALY DETECTION</span>
          <h1>Data quality analysis studio</h1>
          <p>Thực thi dbt tests, phân tích tín hiệu bất thường và tổng hợp báo cáo Data Steward.</p>
        </div></div>
      </div>
      <div className="analysis-hero-actions">
        {run && TERMINAL.has(run.status) && onRerun && <button type="button" className="button secondary" disabled={rerunBusy} onClick={onRerun}>{rerunBusy ? "Đang rerun…" : "Rerun Rule Proposal & Anomaly Detection"}</button>}
        <button type="button" className="button secondary analysis-back" onClick={onBackToGraph1}><ArrowLeft aria-hidden="true" /> Profiler</button>
      </div>
    </header>

    {error && <div className="analysis-alert danger" role="alert"><XCircle aria-hidden="true" /><div><strong>Không thể tải Analysis Studio</strong><span>{error}</span></div><button type="button" onClick={() => void refresh()}>Thử lại</button></div>}
    <div className="analysis-sr-status" aria-live="polite">{statusMessage}</div>

    <section className="analysis-runbar" aria-label="Analysis run status">
      <div className="analysis-run-id"><span className={`analysis-live-dot ${run?.status?.toLowerCase() ?? "pending"}`} /><div><strong>{run?.id ?? analysisRunId}</strong><span>{run?.dataset_id ?? "Đang tải dataset"}</span></div></div>
      <div className="analysis-run-meta"><span><small>STATUS</small><strong>{run?.status ?? "LOADING"}</strong></span><span><small>PHASE</small><strong>{displayAnalysisArea(run?.phase ?? "PREPARING")}</strong></span><span><small>CURRENT</small><strong>{run?.current_node ?? "QUEUED"}</strong></span><span><small>NODES</small><strong>{completedNodes}/{nodes.length || 10}</strong></span></div>
      <div className="analysis-progress"><span style={{ width: `${progress}%` }} /></div>
    </section>

    <section className="analysis-timeline" aria-label="Rule proposal and anomaly detection execution path">
      {nodes.map((node) => <div key={node.node_key} className={`analysis-node ${node.status.toLowerCase()}`}>
        <span className="analysis-node-icon">{statusIcon(node.status)}</span><div><small>{displayAnalysisArea(node.graph_name)}</small><strong>{NODE_LABELS[node.node_key] ?? node.node_key}</strong><span>{node.status} · {formatDuration(node.duration_ms)}</span></div>
      </div>)}
    </section>

    <div className="analysis-layout">
      <aside className="analysis-report-panel">
        <header className="analysis-panel-header"><div><FileText aria-hidden="true" /><div><span>PANEL 1</span><h2>Data Steward report</h2></div></div>{report?.available && <span className={`analysis-source ${report.source?.toLowerCase()}`}>{report.source === "LLM" ? "LLM GENERATED" : "DETERMINISTIC FALLBACK"}</span>}</header>
        {report?.available ? <>
          <div className="analysis-report-actions"><button type="button" onClick={() => void copyReport()}><Copy aria-hidden="true" /> Sao chép</button>{reportDownloadable ? <button type="button" onClick={downloadReport}><Download aria-hidden="true" /> Tải Markdown</button> : <span role="status">Artifact chưa được publish; không thể tải xuống.</span>}</div>
          {toc.length > 0 && <nav className="analysis-toc" aria-label="Mục lục báo cáo"><strong>Mục lục</strong>{toc.map((item) => <a key={item.id} href={`#${item.id}`}>{item.title}</a>)}</nav>}
          <article className="analysis-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
              h2: ({ children }) => <h2 id={slugify(String(children))}>{children}</h2>,
            }}>{report.markdown}</ReactMarkdown>
          </article>
        </> : <div className="analysis-report-empty" role="status"><span className="analysis-spinner" /><strong>Đang chuẩn bị báo cáo</strong><p>Report Writer chạy sau khi Rule Proposal và Anomaly Detection hoàn tất. Kết quả kiểm thử bên phải sẽ xuất hiện trước.</p></div>}
      </aside>

      <div className="analysis-results-column">
        <section className="analysis-panel analysis-graph2">
          <header className="analysis-panel-header"><div><Database aria-hidden="true" /><div><span>PANEL 2.1 · RULE PROPOSAL</span><h2>dbt test execution</h2><p>Kết quả thật từ Test Generator, validator, runner và persistence.</p></div></div><span className={`analysis-chip ${graph2?.available ? "success" : "pending"}`}>{graph2?.available ? "DATA AVAILABLE" : "WAITING"}</span></header>
          <div className="analysis-panel-body">
            <div className="analysis-kpi-grid">
              <Metric label="TOTAL RULES" value={summary?.total ?? 0} />
              <Metric label="PASS" value={summary?.passed ?? 0} tone="success" />
              <Metric label="FAIL" value={summary?.failed ?? 0} tone="danger" />
              <Metric label="ERROR" value={summary?.errors ?? 0} tone="warning" />
              <Metric label="SKIPPED" value={summary?.skipped ?? 0} />
              <Metric label="FAILED ROWS" value={formatNumber(summary?.total_failed ?? 0)} tone="danger" />
            </div>

            <div className="analysis-chart-grid">
              <figure className="analysis-chart-card"><figcaption><strong>Phân bố trạng thái</strong><span>{summary?.total ?? 0} rule results</span></figcaption><div className="analysis-chart" role="img" aria-label={`Phân bố kết quả: ${summary?.passed ?? 0} pass, ${summary?.failed ?? 0} fail, ${summary?.errors ?? 0} error, ${summary?.skipped ?? 0} skipped`}>
                {statusData.length ? <ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={statusData} dataKey="value" nameKey="name" innerRadius={48} outerRadius={76} paddingAngle={3}>{statusData.map((item) => <Cell key={item.name} fill={STATUS_COLORS[item.name]} />)}</Pie><Tooltip formatter={(value) => formatNumber(Number(value))} /></PieChart></ResponsiveContainer> : <span className="analysis-chart-empty">Chưa có kết quả</span>}
              </div><div className="analysis-chart-legend">{statusData.map((item) => <span key={item.name}><i style={{ background: STATUS_COLORS[item.name] }} />{item.name} <strong>{item.value}</strong></span>)}</div></figure>
              <figure className="analysis-chart-card wide"><figcaption><strong>Top tỷ lệ vi phạm</strong><span>Dữ liệu thật, sắp xếp giảm dần</span></figcaption><div className="analysis-chart" role="img" aria-label="Biểu đồ tỷ lệ vi phạm cao nhất theo rule">
                {violationData.length ? <ResponsiveContainer width="100%" height="100%"><BarChart data={violationData} layout="vertical" margin={{ left: 12, right: 18 }}><CartesianGrid strokeDasharray="3 3" horizontal={false} /><XAxis type="number" unit="%" domain={[0, "dataMax"]} tick={{ fontSize: 10 }} /><YAxis type="category" dataKey="name" width={145} tick={{ fontSize: 9 }} /><Tooltip formatter={(value) => `${value}%`} /><Bar dataKey="rate" radius={[0, 5, 5, 0]}>{violationData.map((item) => <Cell key={item.name} fill={item.anomaly ? "#ef4444" : "#f59e0b"} />)}</Bar></BarChart></ResponsiveContainer> : <span className="analysis-chart-empty">Chưa có rule vi phạm</span>}
              </div></figure>
            </div>

            <section className="analysis-dbt-strip" aria-label="dbt execution details"><div><small>GENERATED TESTS</small><strong>{graph2?.dbt.generated_tests_count ?? 0}</strong></div><div><small>VALIDATION</small><strong className={graph2?.dbt.validation_skipped ? "warning-text" : ""}>{graph2?.dbt.validation_status ?? "PENDING"}</strong></div><div><small>EXECUTION MODE</small><strong>{graph2?.dbt.execution_mode ?? "pending"}</strong></div><div><small>ARTIFACT</small><strong>{String(graph2?.dbt.artifact.storage_kind ?? "pending")}</strong></div>{graph2?.dbt.validation_skipped && <p><AlertTriangle aria-hidden="true" /> Không tìm thấy dbt executable; metrics được chạy bằng deterministic SQL fallback.</p>}</section>

            <div className="analysis-table-toolbar"><label className="analysis-search"><Search aria-hidden="true" /><span className="analysis-sr-only">Tìm rule</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Tìm rule, table hoặc column" /></label><label><span>Status</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="ALL">Tất cả</option>{["PASS", "FAIL", "ERROR", "SKIPPED", "RESULT_MISMATCH"].map((value) => <option key={value}>{value}</option>)}</select></label><label><span>Severity</span><select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}><option value="ALL">Tất cả</option>{severities.map((value) => <option key={value}>{value}</option>)}</select></label><label><span>Dimension</span><select value={dimensionFilter} onChange={(event) => setDimensionFilter(event.target.value)}><option value="ALL">Tất cả</option>{dimensions.map((value) => <option key={value}>{value}</option>)}</select></label><label><span>Sắp xếp</span><select value={sortBy} onChange={(event) => setSortBy(event.target.value as "violation" | "failed")}><option value="violation">Violation rate</option><option value="failed">Failed rows</option></select></label><label className="analysis-checkbox"><input type="checkbox" checked={anomalyOnly} onChange={(event) => setAnomalyOnly(event.target.checked)} /><span>Chỉ anomaly</span></label></div>

            <div className="analysis-table-wrap"><table className="analysis-table"><caption>Chi tiết kết quả Rule Proposal theo từng rule, table và column</caption><thead><tr><th>Status</th><th>Rule / target</th><th>Risk</th><th>Checked</th><th>Failed</th><th>Violation</th><th>Runtime</th><th>Anomaly</th><th><span className="analysis-sr-only">Chi tiết</span></th></tr></thead><tbody>{filteredRows.map((row) => <ResultRow key={row.rule_id} row={row} expanded={expandedRule === row.rule_id} onToggle={() => setExpandedRule((current) => current === row.rule_id ? "" : row.rule_id)} />)}{!filteredRows.length && <tr><td colSpan={9} className="analysis-empty-row">Không có kết quả phù hợp bộ lọc.</td></tr>}</tbody></table></div>
          </div>
        </section>

        <section className="analysis-panel analysis-graph3">
          <header className="analysis-panel-header"><div><ShieldAlert aria-hidden="true" /><div><span>PANEL 2.2 · ANOMALY DETECTION</span><h2>Anomaly diagnosis</h2><p>Robust statistical signals, hypotheses và evidence đã persist.</p></div></div><span className={`analysis-chip ${graph3?.available ? "success" : "pending"}`}>{graph3?.available ? "ANALYZED" : "WAITING"}</span></header>
          <div className="analysis-panel-body">
            {graph3?.decision ? <div className={`analysis-decision ${graph3.decision.severity.toLowerCase()}`}><div><span>ANOMALY DECISION</span><strong>{graph3.decision.decision}</strong><p>{graph3.decision.override_reason || `Dominant signal family: ${graph3.decision.dominant_family ?? "N/A"}`}</p></div><div className="analysis-decision-score"><strong>{Math.round(graph3.decision.score * 100)}</strong><span>/100 score</span></div><div><small>CONFIDENCE</small><strong>{Math.round(graph3.decision.confidence * 100)}%</strong><small>SEVERITY</small><strong>{graph3.decision.severity}</strong></div></div> : <div className="analysis-pending-block"><span className="analysis-spinner" /><div><strong>Anomaly Detection đang chờ kết quả Rule Proposal</strong><p>Signals và hypotheses sẽ xuất hiện sau bước anomaly detector.</p></div></div>}

            <div className="analysis-chart-card analysis-signal-chart"><figcaption><strong>Tín hiệu bất thường xếp hạng</strong><span>Score và target từ anomaly_detector_node</span></figcaption><div className="analysis-chart tall" role="img" aria-label="Biểu đồ điểm số các tín hiệu bất thường cao nhất">{signalData.length ? <ResponsiveContainer width="100%" height="100%"><BarChart data={signalData} layout="vertical" margin={{ left: 12, right: 18 }}><CartesianGrid strokeDasharray="3 3" horizontal={false} /><XAxis type="number" unit="%" domain={[0, 100]} tick={{ fontSize: 10 }} /><YAxis type="category" dataKey="name" width={160} tick={{ fontSize: 9 }} /><Tooltip formatter={(value) => `${value}%`} /><Bar dataKey="score" fill="#ef4444" radius={[0, 5, 5, 0]} /></BarChart></ResponsiveContainer> : <span className="analysis-chart-empty">Chưa có tín hiệu</span>}</div></div>

            <div className="analysis-table-wrap"><table className="analysis-table signal"><caption>Chi tiết tín hiệu Anomaly Detection</caption><thead><tr><th>Family / detector</th><th>Target</th><th>Score</th><th>Reliability</th><th>Observed / baseline</th><th>Explanation</th></tr></thead><tbody>{(graph3?.signals ?? []).map((signal) => <tr key={signal.signal_id}><td><strong>{signal.family}</strong><small>{signal.detector_name} · v{signal.detector_version}</small></td><td><code>{signal.target_id}</code><small>{signal.target_type} · {signal.sufficient_history ? "Historical" : "Cold start"}</small></td><td><strong className={signal.score >= .7 ? "danger-text" : ""}>{Math.round(signal.score * 100)}%</strong></td><td>{Math.round(signal.reliability * 100)}%</td><td><code>{signal.observed_value ?? "—"}</code><small>{JSON.stringify(signal.baseline)}</small></td><td>{signal.explanation}<small>{signal.evidence_refs.join(" · ") || "Không có evidence ref"}</small></td></tr>)}{!graph3?.signals.length && <tr><td colSpan={6} className="analysis-empty-row">Chưa có anomaly signal.</td></tr>}</tbody></table></div>

            <section className="analysis-hypotheses"><div className="analysis-section-title"><div><Activity aria-hidden="true" /><div><span>ROOT CAUSE REASONING</span><h3>Giả thuyết nguyên nhân</h3></div></div><strong>{graph3?.hypotheses.length ?? 0} hypotheses</strong></div>{(graph3?.hypotheses ?? []).map((hypothesis, index) => <article key={hypothesis.id}><header><span>HYPOTHESIS {index + 1} · {hypothesis.hypothesis_type}</span><strong>{Math.round(hypothesis.confidence * 100)}% confidence</strong></header><h4>{hypothesis.summary}</h4><div className="analysis-hypothesis-grid"><div><small>SUPPORTING SIGNALS</small><p>{hypothesis.supporting_signal_ids.join(" · ") || "Không có"}</p></div><div><small>EVIDENCE</small><p>{hypothesis.evidence_refs.join(" · ") || "Không có"}</p></div></div>{hypothesis.recommended_checks.length > 0 && <div className="analysis-checks"><strong>Recommended checks</strong><ol>{hypothesis.recommended_checks.map((check) => <li key={check}>{check}</li>)}</ol></div>}<footer><span>{hypothesis.model_name} · prompt {hypothesis.prompt_version} · {formatDuration(hypothesis.latency_ms)}</span>{hypothesis.fallback_used && <span className="analysis-source fallback">FALLBACK</span>}</footer></article>)}{!graph3?.hypotheses.length && <div className="analysis-empty-card">Chưa có giả thuyết hoặc decision hiện tại không yêu cầu phân tích nguyên nhân.</div>}</section>
          </div>
        </section>
      </div>
    </div>
  </main>;
}

function ResultRow({ row, expanded, onToggle }: { row: AnalysisGraph2Result; expanded: boolean; onToggle: () => void }) {
  return <>
    <tr className={`${row.status.toLowerCase()} ${row.anomaly.flagged ? "anomaly" : ""}`}>
      <td data-label="Status"><span className={`analysis-result-status ${row.status.toLowerCase()}`}>{row.status === "PASS" ? <CheckCircle2 aria-hidden="true" /> : row.status === "FAIL" ? <XCircle aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}{row.status}</span></td>
      <td data-label="Rule / target"><strong>{row.rule_title}</strong><code>{row.rule_id}</code><small>{row.table_name}.{row.column ?? "table-level"} · {row.rule_type}</small></td>
      <td data-label="Risk"><strong>{row.severity}</strong><small>{row.dimension}</small></td>
      <td data-label="Checked">{formatNumber(row.checked_count)}</td>
      <td data-label="Failed"><strong className={row.failed_count ? "danger-text" : ""}>{formatNumber(row.failed_count)}</strong></td>
      <td data-label="Violation"><strong>{formatPercent(row.violation_rate)}</strong></td>
      <td data-label="Runtime">{formatDuration(row.duration_ms)}</td>
      <td data-label="Anomaly">{row.anomaly.flagged ? <span className="analysis-anomaly-badge"><ShieldAlert aria-hidden="true" /> Alert {Math.round((row.anomaly.score ?? 0) * 100)}%</span> : <span className="analysis-muted">—</span>}</td>
      <td><button type="button" className="analysis-expand" aria-label={`${expanded ? "Thu gọn" : "Mở"} chi tiết ${row.rule_title}`} aria-expanded={expanded} onClick={onToggle}>{expanded ? <ChevronUp aria-hidden="true" /> : <ChevronDown aria-hidden="true" />}</button></td>
    </tr>
    {expanded && <tr className="analysis-detail-row"><td colSpan={9}><div><section><span>EXECUTION</span><p>dbt: <strong>{row.dbt_status}</strong> · metrics: <strong>{row.metrics_status}</strong> · duration: <strong>{formatDuration(row.duration_ms)}</strong></p>{row.error && <p className="danger-text">{row.error}</p>}</section><section><span>SAMPLE SOURCE ROW IDS</span><p>{row.sample_row_ids.length ? row.sample_row_ids.join(" · ") : "Không có sample ID vi phạm."}</p></section><section><span>EVIDENCE</span><p>{row.evidence_refs.length ? row.evidence_refs.join(" · ") : "Không có evidence ref."}</p></section>{row.anomaly.flagged && <section className="anomaly-copy"><span>ANOMALY SIGNAL</span><p><strong>{row.anomaly.family}</strong> · reliability {Math.round((row.anomaly.reliability ?? 0) * 100)}%</p><p>{row.anomaly.explanation}</p></section>}</div></td></tr>}
  </>;
}
