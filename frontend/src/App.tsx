/**
 * Giao diện của toàn bộ wizard 5 bước nằm trong chính file này.
 *
 * Bước 2 dùng `OverviewPage`, bước 3 `WorkflowPage`, bước 4 `RunsPage` — tất cả
 * khai báo bên dưới. Bước 1 tách ra `components/wizard/Step1DataPreparation.tsx`
 * và bước 5 ra `components/wizard/Step5Analytics.tsx`.
 *
 * Từng có bốn file `Step1..Step4` trong `components/wizard/` trông y hệt các
 * trang này nhưng không nơi nào import; chúng đã bị xoá ngày 28/08/2026. Nếu
 * cần sửa một bước, sửa ở đây — đừng tạo lại file song song, vì không có gì
 * nhắc người sau rằng bản kia mới là bản chạy thật.
 *
 * Màu sắc lấy qua `var(--…)` khai báo trong `styles.css`; mã hex viết thẳng sẽ
 * không đổi theo chế độ tối.
 */
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { api, isMockMode, workflowApi } from "./api";
import { ApiError, clearApiSession } from "./api/client";
import ThemeControl from "./ThemeControl";
import LanguageToggle from "./LanguageToggle";
import { useI18n } from "./i18n/context";
import { Graph2Analytics } from "./components/wizard/WizardAnalytics";
import { DataExplorerDialog } from "./components/wizard/DataExplorerDialog";
import { Step1DataPreparation } from "./components/wizard/Step1DataPreparation";
import { AnomalyStatisticsPanel } from "./components/wizard/AnomalyStatisticsPanel";
import { DetailOverlay } from "./components/wizard/DetailOverlay";
import { NotificationBell, type AppNotification } from "./components/NotificationBell";
import { DatasetCatalogView } from "./components/wizard/DatasetCatalogView";
import { GraphStagePanel } from "./components/graph/GraphStagePanel";
import { GraphObservatoryPage } from "./components/graph/GraphObservatoryPage";
import { StewardReportPanel } from "./components/graph/StewardReportPanel";
import type {
  ActiveRule,
  AnomalyFeedbackLabel,
  AnomalyHypothesis,
  AnomalySignal,
  AuditLog,
  CreateJobResponse,
  Dataset,
  DatasetRow,
  DatasetRowQuery,
  DatasetRowsResponse,
  DatasetProfile,
  DqResult,
  DqAnomaly,
  DqRun,
  DatasetAccess,
  DatasetAccessLevel,
  Job,
  JobType,
  ManualRuleInput,
  ProposalBasis,
  RuleProposal,
  RuleConfiguration,
  RuleConfigurationInput,
  RuleSpec,
  QualityTrendPoint,
  UserAccount,
  UserCreateInput,
  UserUpdateInput,
  UserRole,
  AgentArtifact,
  ArtifactReviewInput,
  LoopDecisionInput,
  WorkflowRun,
  WorkflowStep,
  WorkflowStepKey,
  GraphCatalog,
  GraphKey,
  NodeRun,
} from "./types";

type View =
  | "overview"
  | "workflow"
  | "datasets"
  | "rules"
  | "runs"
  | "visualization"
  | "data"
  | "audit"
  | "admin"
  | "graphs";

const sleep = (duration: number) =>
  new Promise((resolve) => window.setTimeout(resolve, duration));

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatRule(rule: RuleSpec) {
  if (rule.type === "not_null") return `NOT NULL · ${rule.column}`;
  if (rule.type === "numeric_range")
    return `RANGE · ${rule.column} ≥ ${rule.min_value}`;
  if (rule.type === "accepted_values")
    return `VALUES · ${rule.column} ∈ ${(rule.allowed_values ?? []).join(", ")}`;
  if (rule.type === "cross_field_comparison")
    return `COMPARE · ${(rule.columns ?? []).join(` ${rule.operator ?? "≤"} `)}`;
  return `DUPLICATE · ${(rule.fingerprint_columns ?? []).join(" + ")}`;
}

function getErrorMessage(error: unknown, fallback: string, language: "en" | "vi" = "en") {
  const vi = language === "vi";
  if (!(error instanceof ApiError))
    return error instanceof TypeError
      ? (vi ? "Không thể kết nối tới dịch vụ API. Vui lòng kiểm tra backend local đã chạy chưa, sau đó thử lại." : "Cannot reach the API service. Confirm that the local backend is running, then try again.")
      : error instanceof Error
        ? error.message
        : fallback;
  if (error.status === 401)
    return vi ? "Phiên làm việc đã hết hạn. Vui lòng đăng nhập lại." : "Your session has expired. Please sign in again.";
  if (error.status === 409)
    return (
      error.message || (vi ? "Quy trình không thể tiếp tục ở trạng thái hiện tại." : "The workflow cannot continue from its current state.")
    );
  if (error.status === 422)
    return vi ? "Yêu cầu không hợp lệ với trạng thái quy trình hiện tại." : "The request is not valid for the current workflow state.";
  if (error.status === 429)
    return vi ? "Đã đạt hạn ngạch tài khoản demo. Vui lòng thử lại sau." : "The demo quota has been reached. Please try again later.";
  // A client-side configuration failure is raised locally with a 5xx status and
  // never reached the server, so reporting it as an outage sends the operator
  // to the wrong place. Surface its own message instead.
  if (error.code === "WORKSPACE_NOT_CONFIGURED")
    return error.message;
  if (error.status >= 500)
    return vi ? "Dịch vụ tạm thời không khả dụng. Vui lòng thử lại sau khi hệ thống sẵn sàng." : "The service is temporarily unavailable. Retry when it is ready.";
  return error.message || fallback;
}

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

export function parseApiTimestamp(timestamp: string | number | undefined | null): number {
  if (timestamp === undefined || timestamp === null || timestamp === "") return NaN;
  if (typeof timestamp === "number") return timestamp;
  let str = String(timestamp).trim();
  if (!str) return NaN;
  if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/.test(str)) {
    str = str.replace(" ", "T");
    if (!/Z$|[+-]\d{2}:?\d{2}$/i.test(str)) {
      str = `${str}Z`;
    }
  }
  const time = new Date(str).getTime();
  return Number.isNaN(time) ? NaN : time;
}

/** Seconds since a timestamp, ticking once a second while the panel is open. */
function useElapsedSeconds(since: string | undefined, active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  if (!since) return 0;
  const started = parseApiTimestamp(since);
  return Number.isNaN(started) ? 0 : Math.max(0, Math.floor((now - started) / 1000));
}

function formatElapsed(seconds: number, vi: boolean): string {
  if (seconds < 60) return vi ? `${seconds} giây` : `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return vi ? `${minutes} phút ${rest} giây` : `${minutes}m ${rest}s`;
}

/**
 * Live state of the running job.
 *
 * The backend only writes `job.progress` at a handful of stage boundaries — for
 * PROPOSE_RULES it is 20, then 60, then 100 — and the gap between them is the
 * LLM call, which can run for minutes. The bar therefore sat at 20% looking
 * frozen for most of the job's life.
 *
 * The fix is not to invent a creeping percentage: that would report progress
 * nobody measured. Node telemetry already records each graph node as it
 * finishes, which is real, finer-grained evidence of the same work, so the bar
 * is driven by that when it is available and falls back to the coarse job
 * number when it is not.
 */
function ProgressPanel({
  job,
  title,
  nodeProgress,
}: {
  job: Job;
  title: string;
  nodeProgress?: { done: number; total: number; current?: string; startedAt?: string };
}) {
  const { language } = useI18n();
  const vi = language === "vi";
  const running = job.status === "RUNNING" || job.status === "PENDING";
  const elapsed = useElapsedSeconds(nodeProgress?.startedAt ?? job.created_at, running);

  // Capped below 100: nodes finishing is not the same as the job finishing,
  // and only the job may claim completion.
  const nodeShare =
    nodeProgress && nodeProgress.total > 0
      ? Math.min(95, (nodeProgress.done / nodeProgress.total) * 100)
      : 0;
  const percent = Math.min(100, Math.max(job.progress, nodeShare));

  const translateMessage = (msg: string) => {
    if (!vi || !msg) return msg;
    if (msg.includes("Queued for local worker")) return "Đang chờ worker xử lý…";
    if (msg.includes("Validating manifest")) return "Đang kiểm tra tệp dữ liệu…";
    if (msg.includes("Loading immutable raw rows")) return "Đang nạp các dòng dữ liệu…";
    if (msg.includes("Running dbt build")) return "Đang chạy khởi tạo dbt…";
    if (msg.includes("Persisting aggregate profile")) return "Đang lưu trữ hồ sơ dữ liệu…";
    if (msg.includes("Preparing allow-listed evidence")) return "Đang chuẩn bị dữ liệu bằng chứng…";
    if (msg.includes("Calling local proposal adapter")) return "Đang sinh đề xuất quy tắc…";
    if (msg.includes("Validating typed proposals")) return "Đang kiểm tra đề xuất…";
    if (msg.includes("Persisting proposals")) return "Đang lưu các đề xuất quy tắc…";
    if (msg.includes("Claiming approved rule set")) return "Đang lấy bộ quy tắc đã duyệt…";
    if (msg.includes("Compiling read-only checks")) return "Đang biên dịch quy tắc kiểm tra…";
    if (msg.includes("Executing bounded queries")) return "Đang thực thi truy vấn…";
    if (msg.includes("Persisting results")) return "Đang lưu kết quả kiểm tra…";
    if (msg === "Completed") return "Đã hoàn thành";
    return msg;
  };

  const translateStatus = (status: string) => {
    if (!vi) return status;
    if (status === "SUCCEEDED") return "THÀNH CÔNG";
    if (status === "FAILED") return "THẤT BẠI";
    if (status === "PENDING") return "ĐANG CHỜ";
    if (status === "RUNNING") return "ĐANG CHẠY";
    return status;
  };

  return (
    <div className="progress-panel">
      <div className="progress-heading">
        <div>
          <span className="eyebrow">{vi ? "TÁC VỤ ĐANG CHẠY" : "ACTIVE JOB"}</span>
          <h3>{title}</h3>
        </div>
        <strong>{Math.round(percent)}%</strong>
      </div>
      <div className={`progress-track ${running ? "live" : ""}`}>
        <span style={{ width: `${percent}%` }} />
      </div>
      {nodeProgress && nodeProgress.total > 0 && (
        <div className="progress-nodes">
          <span>
            Node {Math.min(nodeProgress.done + (running ? 1 : 0), nodeProgress.total)}/
            {nodeProgress.total}
            {nodeProgress.current ? ` · ${nodeProgress.current}` : ""}
          </span>
        </div>
      )}
      <div className="progress-meta">
        <span>{translateMessage(job.message)}</span>
        <span>{running ? formatElapsed(elapsed, vi) : translateStatus(job.status)}</span>
      </div>
    </div>
  );
}

function LoginScreen({
  onLogin,
  busy,
  error,
}: {
  onLogin: (username: string, password: string) => void;
  busy: boolean;
  error: string;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onLogin(username, password);
  };
  return (
    <main className="login-shell">
      <div className="login-art">
        <div className="orb orb-one" />
        <div className="orb orb-two" />
        <div className="grid-lines" />
        <div className="brand-lockup">
          <span className="brand-mark">RP</span>
          <span>
            RidePulse <em>DQ</em>
          </span>
        </div>
        <div className="login-pitch">
          <span className="eyebrow">DATA QUALITY INTELLIGENCE</span>
          <h1>
            Turn data signals into <span>trusted decisions.</span>
          </h1>
          <p>
            Inspect the registered mobility dataset, review evidence-grounded
            rules and run only the checks your Steward approves.
          </p>
          <div className="metric-row">
            <div>
              <strong>50k</strong>
              <span>registered rows</span>
            </div>
            <div>
              <strong>5</strong>
              <span>typed rule templates</span>
            </div>
            <div>
              <strong>100%</strong>
              <span>audit visibility</span>
            </div>
          </div>
        </div>
        <div className="login-footer">GATE 2 · COURSE PROJECT SIMULATION</div>
      </div>
      <section className="login-card">
        <div className="mobile-brand">
          <span className="brand-mark">RP</span> RidePulse <em>DQ</em>
        </div>
        <span className="eyebrow">ROLE-BASED ACCESS</span>
        <h2>Welcome back</h2>
        <p className="muted">
          Sign in with your demo account to open the workspace.
        </p>
        <form onSubmit={submit}>
          <label htmlFor="username">Username</label>
          <input
            id="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="user, steward, or admin"
            autoFocus
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Enter password"
          />
          {error && <div className="inline-error">{error}</div>}
          <button
            className="button primary full"
            disabled={busy || username.length < 1 || password.length < 1}
          >
            {busy ? "Opening workspace…" : "Open workspace →"}
          </button>
        </form>
        <div className="login-note">
          <span className="lock-icon">⌁</span>
          <span>
            <strong>Demo accounts</strong>
            <br />
            <code>user/user</code> read-only · <code>steward/steward</code>{" "}
            review · <code>admin/admin</code> full access.
          </span>
        </div>
      </section>
    </main>
  );
}

const workflowStepLabels: Record<
  WorkflowStepKey,
  { label: string; owner: string; description: string }
> = {
  UPLOAD_PROFILE: {
    label: "Prepare dataset",
    owner: "System",
    description:
      "Internal system step: register the dataset and build its deterministic aggregate profile before the agent starts.",
  },
  UNDERSTAND_DATA: {
    label: "Understand data",
    owner: "Agent",
    description:
      "Inspect the profiled rows, inferred schema and semantic contract before you continue to rule generation.",
  },
  PROPOSE_RULES: {
    label: "Propose rules",
    owner: "Agent",
    description: "Generate typed rules with evidence and confidence.",
  },
  REVIEW_RULES: {
    label: "Review rules",
    owner: "Steward",
    description: "Approve, request changes or reject the rule set.",
  },
  PUBLISH_RULESET: {
    label: "Publish ruleset",
    owner: "Steward",
    description:
      "Create an immutable ruleset version from the approved typed rules.",
  },
  RUN_CHECKS: {
    label: "Run quality checks",
    owner: "Runner",
    description: "Run the approved checks.",
  },
  ANALYZE_REPORT: {
    label: "Analyze and report",
    owner: "Agent",
    description: "Summarize the run results.",
  },
  PROPOSE_CODE: {
    label: "Propose standardization",
    owner: "Agent",
    description: "Create a deterministic code or transformation plan.",
  },
  REVIEW_EXECUTE: {
    label: "Review and execute",
    owner: "Steward",
    description: "Validate the code proposal before a bounded run.",
  },
  ANALYZE_IMPROVE: {
    label: "Analyze and improve",
    owner: "Loop Agent",
    description: "Explain results and propose a bounded next iteration.",
  },
};

const workflowPhases = [
  {
    label: "Propose & review",
    owner: "Agent + steward",
    steps: ["PROPOSE_RULES", "REVIEW_RULES"] as WorkflowStepKey[],
  },
  {
    label: "Publish & monitor",
    owner: "System + agent",
    steps: [
      "PUBLISH_RULESET",
      "RUN_CHECKS",
      "ANALYZE_REPORT",
    ] as WorkflowStepKey[],
  },
];

/** Which graph each job type executes, for reading its node telemetry. */
const jobGraphKey: Partial<Record<JobType, GraphKey>> = {
  UNDERSTAND_DATA: "G1A",
  PROPOSE_RULES: "G1B",
  GRAPH1_EXECUTION: "G1_FULL",
  GRAPH1_CONTINUATION: "G1_FULL",
  RUN_DQ: "G2",
  ANALYSIS_GRAPH2_GRAPH3: "G3",
};

function workflowPhaseIndex(step: WorkflowStepKey) {
  return workflowPhases.findIndex((phase) => phase.steps.includes(step));
}

/**
 * Graph 1A's output: the semantic contract the agent inferred.
 *
 * This used to sit at the bottom of the dataset catalogue, which put a step-2
 * artifact on the step-1 screen. It lives beside the Graph 1A node view now, so
 * the run and the thing the run produced are on the same page.
 */
function SemanticContractPanel({
  workflow,
  artifacts,
  dataset,
  profile,
  canOperate,
  busy,
  onStartUnderstand,
  onConfirmContract,
  language,
}: {
  workflow: WorkflowRun | null;
  artifacts: AgentArtifact[];
  dataset?: Dataset;
  profile: DatasetProfile | null;
  canOperate: boolean;
  busy: boolean;
  onStartUnderstand: (datasetId: string) => void;
  onConfirmContract: (artifact: AgentArtifact) => void;
  language: "en" | "vi";
}) {
  const { t } = useI18n();
  const activeArtifact = workflow && dataset && workflow.dataset_id === dataset.id
    ? workflowArtifactForStep(workflow, artifacts, "UNDERSTAND_DATA")
    : undefined;

  const payload = activeArtifact?.payload && typeof activeArtifact.payload === "object"
    ? (activeArtifact.payload as Record<string, unknown>)
    : null;

  // The confirm endpoint reports "CONFIRMED"; "APPROVED" is what the generic
  // artifact review flow writes. Either one means it is signed off.
  const confirmed =
    activeArtifact?.status === "CONFIRMED" || activeArtifact?.status === "APPROVED";

  const contractColumns = payload && Array.isArray(payload.columns)
    ? (payload.columns.filter((column): column is Record<string, unknown> =>
        Boolean(column && typeof column === "object"),
      ))
    : [];

  if (!dataset) return null;

  return (
    <div className="datasets-page">
      {dataset && (
        <section className="panel" style={{ padding: "24px" }}>
          <div className="panel-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <span className="eyebrow">{t("datasets.agentCapability")}</span>
              <h2>{t("datasets.understandAgentTitle", { name: dataset!.name })}</h2>
              <p className="muted">{t("datasets.understandAgentDesc")}</p>
            </div>
            <div className="contract-actions">
              <button
                className="button secondary"
                disabled={!canOperate || busy}
                onClick={() => onStartUnderstand(dataset.id)}
              >
                {busy
                  ? language === "vi" ? "Đang phân tích…" : "Running analysis…"
                  : payload
                    ? language === "vi" ? "↻ Chạy lại" : "↻ Run again"
                    : language === "vi" ? "⚡ Chạy agent hiểu dữ liệu" : "⚡ Run Understand Agent"}
              </button>
              {/* The confirm endpoint has existed since the workflow was built
                  but nothing ever called it, so the contract could be produced
                  and never signed off. */}
              {payload && activeArtifact && (
                <button
                  className="button primary"
                  disabled={!canOperate || busy || confirmed}
                  onClick={() => onConfirmContract(activeArtifact)}
                >
                  {confirmed
                    ? language === "vi" ? "✓ Đã xác nhận" : "✓ Confirmed"
                    : language === "vi" ? "Xác nhận hợp đồng" : "Confirm contract"}
                </button>
              )}
            </div>
          </div>

          {payload ? (
            <div className="understanding-holder" style={{ marginTop: "16px" }}>
              <div className="understanding-summary" style={{ padding: "16px", background: "var(--surface-muted, #f8fafc)", borderRadius: "8px", borderLeft: "4px solid var(--accent, #2563eb)" }}>
                <span className="eyebrow">
                  {t("datasets.semanticContract")} · {t("datasets.mode")}: {String(payload.agent_mode ?? "profile-backed").toUpperCase()}
                </span>
                <p style={{ marginTop: "8px", fontSize: "15px", lineHeight: "1.5", color: "var(--ink)" }}>
                  {String(payload.summary ?? t("datasets.agentAnalysisCompleted"))}
                </p>
              </div>

              <div className="understanding-meta" style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "12px", marginTop: "16px", marginBottom: "20px" }}>
                <div>
                  <span>{t("datasets.rows")}</span>
                  <strong>{(profile?.row_count ?? dataset.row_count).toLocaleString()}</strong>
                </div>
                <div>
                  <span>{t("datasets.completenessScore")}</span>
                  <strong>{profile ? `${profile.completeness_score.toFixed(1)}%` : "—"}</strong>
                </div>
                <div>
                  <span>{t("datasets.validityScore")}</span>
                  <strong>{profile ? `${profile.validity_score.toFixed(1)}%` : "—"}</strong>
                </div>
                <div>
                  <span>{t("datasets.artifactStatus")}</span>
                  <strong>
                    {activeArtifact?.status === "CONFIRMED"
                      ? (language === "vi" ? "ĐÃ XÁC NHẬN" : "CONFIRMED")
                      : activeArtifact?.status === "APPROVED"
                        ? (language === "vi" ? "ĐÃ DUYỆT" : "APPROVED")
                        : activeArtifact?.status === "VALIDATED"
                          ? (language === "vi" ? "ĐÃ KIỂM ĐỊNH" : "VALIDATED")
                          : activeArtifact?.status ?? (language === "vi" ? "ĐÃ KIỂM ĐỊNH" : "VALIDATED")}
                  </strong>
                </div>
              </div>

              {/* SEMANTIC CONTRACT INFERRED SCHEMA TABLE */}
              {contractColumns.length > 0 && (
                <div className="understanding-section" style={{ marginTop: "20px" }}>
                  <div className="panel-heading" style={{ marginBottom: "12px" }}>
                    <div>
                      <span className="eyebrow">{t("datasets.semanticContract")}</span>
                      <h3 style={{ margin: 0 }}>{t("datasets.inferredSchemas", { count: contractColumns.length })}</h3>
                    </div>
                  </div>
                  <div style={{ overflowX: "auto", border: "1px solid var(--border, #e2e8f0)", borderRadius: "8px" }}>
                    <div className="semantic-schema-grid">
                      <div className="semantic-schema-grid-header">
                        <tr style={{ background: "var(--surface-muted, #f1f5f9)", textAlign: "left", borderBottom: "2px solid var(--border, #cbd5e1)" }}>
                          <th style={{ padding: "10px 12px" }}>{t("datasets.colName")}</th>
                          <th style={{ padding: "10px 12px" }}>{t("datasets.colSemanticType")}</th>
                          <th style={{ padding: "10px 12px" }}>{t("datasets.colNullable")}</th>
                          <th style={{ padding: "10px 12px" }}>{t("datasets.colConfidence")}</th>
                          <th style={{ padding: "10px 12px" }}>{t("datasets.colDescription")}</th>
                        </tr>
                      </div>
                      <div className="semantic-schema-grid-items">
                        {contractColumns.map((col, idx) => (
                          <article className="semantic-schema-card" key={String(col.name ?? idx)}>
                            <td style={{ padding: "10px 12px", fontWeight: 600 }}><code>{String(col.name ?? "")}</code></td>
                            <td style={{ padding: "10px 12px" }}><span className="status-pill info">{String(col.semantic_type ?? "unknown")}</span></td>
                            <td style={{ padding: "10px 12px" }}>{col.nullable ? t("datasets.yes") : t("datasets.no")}</td>
                            <td style={{ padding: "10px 12px" }}>
                              <span className={`confidence-value ${Number(col.confidence ?? 0) >= 0.8 ? "high" : "low"}`}>
                                {typeof col.confidence === "number" ? `${(col.confidence * 100).toFixed(0)}%` : "N/A"}
                              </span>
                            </td>
                            <td style={{ padding: "10px 12px", color: "var(--muted)", fontSize: "12px" }}>
                              {String(col.description ?? col.reasoning ?? col.type ?? "—")}
                            </td>
                          </article>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="workflow-artifact-empty" style={{ marginTop: "16px", padding: "20px", background: "var(--color-bg-subtle, #f8fafc)", borderRadius: "8px", textAlign: "center" }}>
              {t("datasets.noUnderstandArtifactDesc")}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function workflowArtifactForStep(
  workflow: WorkflowRun,
  artifacts: AgentArtifact[],
  step: WorkflowStepKey,
) {
  const ids =
    workflow.steps.find((item) => item.key === step)?.artifact_ids ?? [];
  return [...artifacts].reverse().find((artifact) => ids.includes(artifact.id));
}

function WorkflowPage({
  dataset,
  profile,
  datasets,
  workflow,
  artifacts,
  proposals,
  configurations,
  activeJob,
  busy,
  canOperate,
  onStartStep,
  onAdvanceStep,
  onReviewArtifact,
  onLoopDecision,
  onApproveRule,
  onRejectRule,
  onEditRule,
  onDeleteRule,
  onSaveConfiguration,
  onCreateManualRule,
  onRewindStep,
  onSelectDataset,
  onUploadPreview,
  onBackToDatasetSelection,
  graphPanel,
  nodeProgress,
}: {
  dataset?: Dataset;
  profile: DatasetProfile | null;
  datasets: Dataset[];
  workflow: WorkflowRun | null;
  artifacts: AgentArtifact[];
  proposals: RuleProposal[];
  configurations: RuleConfiguration[];
  activeJob: Job | null;
  busy: boolean;
  canOperate: boolean;
  onStartStep: (step: WorkflowStepKey, fresh?: boolean) => void;
  onAdvanceStep: () => void;
  onReviewArtifact: (artifactId: string, input: ArtifactReviewInput) => void;
  onLoopDecision: (input: LoopDecisionInput) => void;
  onApproveRule: (id: string) => void;
  onRejectRule: (id: string) => void;
  onEditRule: (proposal: RuleProposal) => void;
  onDeleteRule: (id: string) => void;
  onSaveConfiguration: (id: string, input: RuleConfigurationInput) => void;
  onCreateManualRule: () => void;
  onRewindStep: (step: WorkflowStepKey) => void;
  onSelectDataset: (datasetId: string) => void;
  onUploadPreview: (file: File) => void;
  onBackToDatasetSelection: () => void;
  nodeProgress?: { done: number; total: number; current?: string; startedAt?: string };
  /** Graph 1B node detail, wired by App so this page stays presentational. */
  graphPanel?: ReactNode;
}) {
  const [ruleDatasetId, setRuleDatasetId] = useState(dataset?.id ?? "");
  useEffect(() => {
    setRuleDatasetId(workflow?.dataset_id ?? dataset?.id ?? "");
  }, [workflow?.dataset_id, dataset?.id]);
  if (!workflow) {
    const selectedRuleDataset = datasets.find(
      (item) => item.id === ruleDatasetId,
    );
    return (
      <section className="workflow-page">
        <header className="workflow-page-header">
          <div>
            <span className="eyebrow">RULE PROPOSER WORKFLOW</span>
            <h1>Dataset to decision</h1>
            <p>
              {selectedRuleDataset?.name ?? "Choose a registered dataset"} ·
              each result is preserved as a versioned workflow artifact.
            </p>
          </div>
          <span className="status-pill">
            {selectedRuleDataset ? "READY" : "SELECT DATASET"}
          </span>
        </header>
        <div className="workflow-layout">
          <aside className="workflow-stepper" aria-label="Four workflow phases">
            {workflowPhases.map((phase, index) => (
              <button
                type="button"
                className={`workflow-step ${index === 0 ? "active" : "locked"}`}
                key={phase.label}
                disabled
              >
                <div className="workflow-step-index">{index + 1}</div>
                <div>
                  <strong>{phase.label}</strong>
                  <span>{phase.owner}</span>
                </div>
              </button>
            ))}
          </aside>
          <section className="workflow-detail panel workflow-selection-detail">
            <div className="workflow-detail-heading">
              <div>
                <span className="eyebrow">STEP 0 · CHOOSE DATASET</span>
                <h2>Select the dataset for this run</h2>
                <p>Choose a profiled dataset to start.</p>
              </div>
              <span className="status-pill">
                {selectedRuleDataset ? "DATASET SELECTED" : "READY"}
              </span>
            </div>
            <div className="dataset-selection-holder">
              <div className="section-heading">
                <div>
                  <span className="eyebrow">REGISTERED DATASETS</span>
                  <h3>Select an input</h3>
                </div>
                <span className="muted">{datasets.length} available</span>
              </div>
              <div className="dataset-choice-list">
                <label className="dataset-choice dataset-choice-import">
                  <input type="file" accept=".csv,.parquet,text/csv,application/vnd.apache.parquet" disabled={!canOperate || busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) onUploadPreview(file); event.currentTarget.value = ""; }} />
                  <span className="dataset-choice-import-icon">+</span>
                  <span><strong>Import dataset</strong><small>CSV or Parquet · profile automatically</small></span>
                </label>
                {datasets.map((item) => (
                  <button
                    type="button"
                    className={`dataset-choice ${item.id === ruleDatasetId ? "selected" : ""}`}
                    key={item.id}
                    disabled={busy}
                    onClick={() => {
                      setRuleDatasetId(item.id);
                      onSelectDataset(item.id);
                    }}
                  >
                    <span>
                      <strong>{item.name}</strong>
                      <small>
                        {item.row_count.toLocaleString()} rows ·{" "}
                        {item.manifest_version}
                      </small>
                    </span>
                    <span>{item.status.replaceAll("_", " ")}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="workflow-actions">
              <button
                className="button primary"
                onClick={() =>
                  onStartStep(
                    "PROPOSE_RULES",
                    true,
                  )
                }
                disabled={
                  !canOperate ||
                  busy ||
                  Boolean(activeJob) ||
                  !selectedRuleDataset
                }
              >
                Start Rule Proposer <span aria-hidden="true">→</span>
              </button>
              <small>
                Rule proposals will be generated based on the dataset profile and semantic contract.
              </small>
              {!canOperate && (
                <small>Steward access is required to start a workflow.</small>
              )}
            </div>
          </section>
        </div>
      </section>
    );
  }
  if (!dataset) {
    return (
      <div className="empty-state">
        <span className="eyebrow">WORKFLOW</span>
        <h2>Select a dataset to begin.</h2>
        <p className="muted">
          The workflow will keep every agent artifact scoped to the selected
          dataset.
        </p>
      </div>
    );
  }
  const currentStep = workflow.steps.find(
    (step) => step.key === workflow.current_step,
  );
  const currentArtifact = currentStep
    ? workflowArtifactForStep(workflow, artifacts, currentStep.key)
    : undefined;
  const isRunning =
    Boolean(activeJob) || busy || currentStep?.status === "RUNNING";
  const canRun =
    canOperate &&
    currentStep?.key !== "UPLOAD_PROFILE" &&
    ["READY", "FAILED", "COMPLETED"].includes(currentStep?.status ?? "") &&
    !isRunning;
  const reviewable =
    currentArtifact &&
    currentStep &&
    ["REVIEW_RULES", "REVIEW_EXECUTE", "ANALYZE_IMPROVE"].includes(
      currentStep.key,
    ) &&
    ["WAITING_APPROVAL", "READY"].includes(currentStep.status) &&
    ["DRAFT", "VALIDATED", "APPROVED"].includes(currentArtifact.status);
  const rulesDecided =
    proposals.length > 0 &&
    proposals.some((proposal) => proposal.status === "APPROVED") &&
    proposals.every((proposal) =>
      ["APPROVED", "REJECTED"].includes(proposal.status),
    );
  const renderArtifact = (artifact = currentArtifact) => {
    const payload =
      artifact?.payload && typeof artifact.payload === "object"
        ? (artifact.payload as Record<string, unknown>)
        : null;
    if (!artifact || !payload)
      return (
        <div className="workflow-artifact-empty">
          This step has not produced an artifact yet.
        </div>
      );
    if (artifact.type === "SEMANTIC_CONTRACT") {
      const contractColumns = Array.isArray(payload.columns)
        ? payload.columns.filter((column): column is Record<string, unknown> =>
            Boolean(column && typeof column === "object"),
          )
        : [];
      const lowConfidenceColumns = contractColumns.filter(
        (column) =>
          typeof column.confidence === "number" && column.confidence < 0.8,
      ).length;
      return (
        <div className="understanding-holder">
          <div className="understanding-summary">
            <span className="eyebrow">
              DATA UNDERSTANDING ·{" "}
              {String(payload.agent_mode ?? "profile-backed")}
            </span>
            <p>
              {String(
                payload.summary ?? "Agent has not supplied a summary yet.",
              )}
            </p>
          </div>
          <div className="understanding-meta">
            <div>
              <span>Rows</span>
              <strong>
                {(profile?.row_count ?? dataset.row_count).toLocaleString()}
              </strong>
            </div>
            <div>
              <span>Columns</span>
              <strong>
                {(
                  profile?.columns.length ?? contractColumns.length
                ).toLocaleString()}
              </strong>
            </div>
            <div>
              <span>Completeness</span>
              <strong>
                {profile ? `${profile.completeness_score.toFixed(1)}%` : "—"}
              </strong>
            </div>
            <div>
              <span>Validity</span>
              <strong>
                {profile ? `${profile.validity_score.toFixed(1)}%` : "—"}
              </strong>
            </div>
            <div>
              <span>Source</span>
              <strong>{dataset.source_label}</strong>
            </div>
            <div>
              <span>Manifest</span>
              <strong>{dataset.manifest_version}</strong>
            </div>
          </div>
          <div className="understanding-section">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">INFERRED SCHEMA</span>
                <h3>Semantic columns</h3>
              </div>
              <span className="muted">
                {contractColumns.length} mapped
                {lowConfidenceColumns > 0 && (
                  <> · <strong className="needs-review">{lowConfidenceColumns} cần xem lại</strong></>
                )}
              </span>
            </div>
            {lowConfidenceColumns > 0 && (
              <p className="schema-hint">
                Cột được đánh dấu là nơi agent suy luận kém chắc chắn nhất — duyệt
                từ đó trước, vì mọi luật sinh ra sau này đều dựa trên hợp đồng này.
              </p>
            )}
            {/* Evidence keys are namespaced `profile.column.<name>.<signal>`, so
                each one belongs to a column. Listing them all in a separate
                block at the bottom meant reading a wall of 200 chips and
                mentally re-joining them to the rows above. */}
            <div className="schema-list">
              {contractColumns.map((column) => {
                const columnName = String(column.name ?? "");
                const columnSignals = Array.isArray(payload.evidence)
                  ? payload.evidence
                      .map(String)
                      .filter((key) => key.startsWith(`profile.column.${columnName}.`))
                      .map((key) => key.slice(`profile.column.${columnName}.`.length))
                  : [];
                // Ngưỡng 0.8 trùng với ngưỡng dùng ở bảng độ tin cậy bước 1, để
                // "kém chắc chắn" mang cùng một nghĩa ở mọi màn hình.
                const score =
                  typeof column.confidence === "number" ? column.confidence : null;
                const uncertain = score !== null && score < 0.8;
                return (
                  <div
                    className={`schema-row${uncertain ? " uncertain" : ""}`}
                    key={String(column.name)}
                  >
                    <strong>{columnName || "Unnamed column"}</strong>
                    <span>{String(column.semantic_type ?? "unknown")}</span>
                    <small className={uncertain ? "low" : undefined}>
                      {score === null
                        ? "Không có điểm tin cậy"
                        : `${Math.round(score * 100)}% tin cậy`}
                    </small>
                    {columnSignals.length > 0 && (
                      <div className="schema-signals">
                        {columnSignals.map((signal) => (
                          <code key={signal} title={`profile.column.${columnName}.${signal}`}>
                            {signal}
                          </code>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
          <div className="understanding-section">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">PROFILE EVIDENCE</span>
                <h3>Signals used by Agent</h3>
              </div>
            </div>
            <div className="evidence-list">
              {/* Only the signals that describe the dataset as a whole; the
                  per-column ones now sit on their own rows above. */}
              {Array.isArray(payload.evidence) &&
                payload.evidence
                  .map(String)
                  .filter((evidence) => !evidence.startsWith("profile.column."))
                  .map((evidence) => (
                    <span key={evidence} className="evidence-chip">
                      {evidence}
                    </span>
                  ))}
            </div>
          </div>
        </div>
      );
    }
    if (artifact.type === "CODE_PROPOSAL")
      return (
        <>
          <div className="artifact-code">
            <code>{`-- ${String(payload.target ?? "standardized_dataset")}`}</code>
            <code>select * from source_dataset</code>
            <code>-- normalize timestamps to UTC</code>
            <code>-- trim controlled categorical values</code>
          </div>
          <div className="artifact-meta">
            <span>
              Deterministic:{" "}
              {String(
                (payload.validation as Record<string, unknown> | undefined)
                  ?.deterministic ?? true,
              )}
            </span>
            <span>
              Destructive:{" "}
              {String(
                (payload.validation as Record<string, unknown> | undefined)
                  ?.destructive ?? false,
              )}
            </span>
          </div>
        </>
      );
    if (artifact.type === "RULE_SET")
      return (
        <div className="publish-result">
          <div>
            <span className="eyebrow">GRAPH 1b — AI RULE PROPOSAL</span>
            <strong>{String(payload.proposal_count ?? 0)} rules proposed</strong>
          </div>
          <p className="muted" style={{ marginTop: "8px", fontSize: "13px" }}>
            Rules are ready for your review. Approve, edit, or reject each rule before publishing.
          </p>
        </div>
      );
    if (artifact.type === "PUBLISHED_RULESET")
      return (
        <div className="publish-result">
          <div>
            <span className="eyebrow">PUBLISHED</span>
            <strong>{String(payload.rule_count ?? 0)} approved rules</strong>
          </div>
          <dl>
            <div><dt>Ruleset</dt><dd>{String(payload.ruleset_id ?? "—")}</dd></div>
            <div><dt>Version hash</dt><dd>{String(payload.ruleset_hash ?? "—").slice(0, 12)}</dd></div>
          </dl>
        </div>
      );
    if (artifact.type === "DQ_RUN") {
      const dqScore = typeof payload.dq_score === "number" ? (payload.dq_score as number) : null;
      const dqGrade = payload.dq_grade ? String(payload.dq_grade) : null;
      const scoreTone: "danger" | "warning" | "success" =
        dqScore !== null ? (dqScore >= 80 ? "success" : dqScore >= 60 ? "warning" : "danger") : "warning";
      return (
        <>
          <div className="understanding-meta">
            <div>
              <span>Checked</span>
              <strong>
                {Number(payload.total_checked ?? 0).toLocaleString()}
              </strong>
            </div>
            <div>
              <span>Failed</span>
              <strong>
                {Number(payload.total_failed ?? 0).toLocaleString()}
              </strong>
            </div>
            {dqScore !== null && (
              <div>
                <span>DQ Score</span>
                <strong style={{ color: scoreTone === "success" ? "var(--success)" : scoreTone === "danger" ? "var(--danger)" : "var(--warn)" }}>
                  {dqScore.toFixed(1)}% {dqGrade ? `(${dqGrade})` : ""}
                </strong>
              </div>
            )}
            <div>
              <span>Run</span>
              <strong>{String(payload.run_id ?? "—")}</strong>
            </div>
          </div>
          <div className="check-result-list">
            {Array.isArray(payload.results) &&
              payload.results.map((item) => {
                const row = item as Record<string, unknown>;
                return (
                  <div className={`check-result-row ${String(row.status).toLowerCase()}`} key={String(row.rule_id)}>
                    <span className="check-result-status">{String(row.status)}</span>
                    <strong>{String(row.title)}</strong>
                    <span>{String(row.failed_count ?? 0)} failed / {String(row.checked_count ?? 0)} checked</span>
                  </div>
                );
              })}
          </div>
        </>
      );
    }
    if (artifact.type === "ANOMALY_REPORT") {
      const decision = String(payload.decision ?? "UNAVAILABLE");
      const score = typeof payload.score === "number" ? (payload.score as number) : null;
      const confidence = typeof payload.confidence === "number"
        ? Math.round((payload.confidence as number) * 100) : null;
      const hypotheses = Array.isArray(payload.hypotheses)
        ? (payload.hypotheses as Record<string, unknown>[]) : [];
      const severityTone: "danger" | "success" | "warning" =
        decision === "ANOMALY" || decision === "CRITICAL" ? "danger"
          : decision === "NORMAL" ? "success" : "warning";
      return (
        <div className="anomaly-report-artifact">
          <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px", flexWrap: "wrap" }}>
            <StatusPill
              label={decision === "INSUFFICIENT_HISTORY" ? "NOT ENOUGH HISTORY" : decision}
              tone={severityTone}
            />
            {score !== null && (
              <span style={{ fontSize: "13px", color: "var(--muted)" }}>
                Score: <strong style={{ color: "var(--ink)" }}>{score.toFixed(1)}</strong>
              </span>
            )}
            {confidence !== null && (
              <span style={{ fontSize: "13px", color: "var(--muted)" }}>
                Confidence: <strong style={{ color: "var(--ink)" }}>{confidence}%</strong>
              </span>
            )}
          </div>
          {hypotheses.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              <span className="eyebrow">AGENT HYPOTHESES</span>
              {hypotheses.map((item, index) => (
                <div key={index} style={{
                  padding: "12px 16px",
                  background: "var(--surface-muted, #f8fafc)",
                  borderRadius: "10px",
                  border: "1px solid var(--border)",
                }}>
                  <p style={{ margin: "0 0 6px", fontWeight: 600, fontSize: "14px" }}>
                    {String(item.summary ?? "No hypothesis supplied.")}
                  </p>
                  {typeof item.confidence === "number" && (
                    <span style={{ fontSize: "12px", color: "var(--muted)" }}>
                      Confidence: {Math.round((item.confidence as number) * 100)}%
                    </span>
                  )}
                  {Array.isArray(item.recommended_checks) && (item.recommended_checks as string[]).length > 0 && (
                    <div style={{ marginTop: "8px", display: "flex", flexWrap: "wrap", gap: "6px" }}>
                      {(item.recommended_checks as string[]).map((check, ci) => (
                        <span key={ci} className="evidence-chip">{check}</span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {Boolean(payload.error) && (
            <p className="muted" style={{ marginTop: "12px", fontSize: "13px" }}>
              ⚠️ Analysis note: {String(payload.error)}
            </p>
          )}
        </div>
      );
    }
    if (artifact.type === "LOOP_RECOMMENDATION")
      return (
        <>
          <p className="hypothesis">
            {String(payload.hypothesis ?? "No hypothesis supplied.")}
          </p>
          <div className="evidence-list">
            {Array.isArray(payload.supporting_signals) &&
              payload.supporting_signals.map((signal) => (
                <span key={String(signal)} className="evidence-chip">
                  {String(signal)}
                </span>
              ))}
          </div>
          <p className="muted">
            Next action:{" "}
            {String(payload.next_action ?? "Review the latest run.")}
          </p>
        </>
      );
    return (
      <>
        <p>
          {String(
            payload.summary ??
            `${String(artifact.type).replaceAll("_", " ")} generated by ${artifact.agent_role}.`,
          )}
        </p>
        <div className="artifact-meta">
          <span>Version {artifact.version}</span>
          <span>{artifact.status}</span>
          {Array.isArray(payload.evidence) && (
            <span>{payload.evidence.length} evidence references</span>
          )}
          {typeof payload.proposal_count === "number" && (
            <span>{payload.proposal_count} typed rules</span>
          )}
        </div>
      </>
    );
  };
  const nextActionLabel =
    currentStep?.key === "UPLOAD_PROFILE"
      ? "Prepare dataset"
      : currentStep?.key === "UNDERSTAND_DATA"
        ? "Run agent understanding"
        : currentStep?.key === "PROPOSE_RULES"
          ? "Generate rule proposals"
          : currentStep?.key === "PUBLISH_RULESET"
            ? "Publish approved rules"
            : currentStep?.key === "RUN_CHECKS"
              ? "Run published checks"
              : currentStep?.key === "PROPOSE_CODE"
                ? "Generate standardization code"
                : "Run current step";
  const visibleWorkflowSteps = workflow.steps;
  const currentPhaseIndex = Math.max(0, workflowPhaseIndex(workflow.current_step));
  const publishPhase = workflowPhases.find((p) => p.steps.includes("PUBLISH_RULESET")) ?? workflowPhases[workflowPhases.length - 1];
  const executionSteps = (publishPhase?.steps ?? [])
    .map((key) => workflow.steps.find((step) => step.key === key))
    .filter((step): step is WorkflowStep => Boolean(step));
  const executionActionLabel = (step: WorkflowStepKey) =>
    step === "PUBLISH_RULESET"
      ? "Publish approved rules"
      : step === "RUN_CHECKS"
        ? "Run Graph 2 checks"
        : "Run Graph 3 analysis";
  const currentStepIndex = currentStep
    ? visibleWorkflowSteps.findIndex((step) => step.key === currentStep.key)
    : -1;
  const nextWorkflowStep =
    currentStepIndex >= 0
      ? visibleWorkflowSteps[currentStepIndex + 1]
      : undefined;
  const canAdvance = Boolean(
    currentStep &&
    ["COMPLETED", "WAITING_APPROVAL"].includes(currentStep.status) &&
    nextWorkflowStep &&
    nextWorkflowStep.status !== "LOCKED" &&
    canOperate &&
    !isRunning,
  );
  const previousWorkflowStep =
    currentStepIndex > 0
      ? visibleWorkflowSteps[currentStepIndex - 1]
      : undefined;
  const canMoveBackward = Boolean(
    previousWorkflowStep && canOperate && !isRunning,
  );
  const canMoveForward = canAdvance;
  return (
    <div className="workflow-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">WORKFLOW RUN {workflow.id}</span>
          <h1>Dataset to decision</h1>
          <p>
            {dataset.name} · revision {workflow.iteration}
          </p>
        </div>
        <div className="page-heading-actions">
          <button
            type="button"
            className="step-nav-button"
            onClick={onBackToDatasetSelection}
            disabled={isRunning}
          >
            Change dataset
          </button>
          <button
            type="button"
            className="step-nav-button backward"
            onClick={() =>
              previousWorkflowStep && onRewindStep(previousWorkflowStep.key)
            }
            disabled={!canMoveBackward}
          >
            ← Back
          </button>
          <button
            type="button"
            className="step-nav-button forward"
            onClick={onAdvanceStep}
            disabled={!canMoveForward}
          >
            Continue →
          </button>
        </div>
      </div>
      {/* The two-phase rail was removed: it was a disabled, non-interactive
          restatement of the wizard stepper already at the top of the page, and
          it took a column of width from the content that matters. */}
      <div className="workflow-layout single">
        <section className="workflow-detail panel">
          <div className="workflow-detail-heading">
            <div>
              <span className="eyebrow">
                CURRENT ACTIVITY · PHASE {currentPhaseIndex + 1}
              </span>
              <h2>
                {currentStep
                  ? workflowStepLabels[currentStep.key].label
                  : "Complete"}
              </h2>
              <p className="muted">
                {currentStep
                  ? workflowStepLabels[currentStep.key].description
                  : "The workflow is complete."}
              </p>
            </div>
            {currentStep && (
              <StatusPill
                label={currentStep.status.replaceAll("_", " ")}
                tone={
                  currentStep.status === "FAILED"
                    ? "danger"
                    : currentStep.status === "WAITING_APPROVAL"
                      ? "warning"
                      : currentStep.status === "COMPLETED"
                        ? "success"
                        : "info"
                }
              />
            )}
          </div>
          {busy && !activeJob && (
            <div className="workflow-pending" role="status" aria-live="polite">
              <span className="workflow-pending-indicator" aria-hidden="true" />
              <div>
                <span className="eyebrow">AGENT IS WORKING</span>
                <strong>
                  Preparing {workflowStepLabels[workflow.current_step].label}
                </strong>
                <p>Agent is running…</p>
              </div>
            </div>
          )}
          {activeJob && (
            <ProgressPanel
              job={activeJob}
              title={`Running ${workflowStepLabels[workflow.current_step].label}`}
              nodeProgress={nodeProgress}
            />
          )}
          {graphPanel}
          {currentPhaseIndex === 3 ? (
            <div className="execution-mini-steps" aria-label="Publish and monitor mini-steps">
              {executionSteps.map((step) => {
                const artifact = workflowArtifactForStep(workflow, artifacts, step.key);
                const isCurrent = step.key === currentStep?.key;
                const stepCanRun =
                  isCurrent &&
                  ["READY", "FAILED"].includes(step.status) &&
                  canRun;
                return (
                  <article className={`execution-mini-step ${isCurrent ? "current" : ""} ${step.status.toLowerCase()}`} key={step.key}>
                    <div className="execution-mini-heading">
                      <div>
                        <span className="eyebrow">{step.key === "RUN_CHECKS" ? "QUALITY CHECKS" : step.key === "ANALYZE_REPORT" ? "ANALYSIS" : "PUBLISHED RULES"}</span>
                        <h3>{workflowStepLabels[step.key].label}</h3>
                        <p>{workflowStepLabels[step.key].description}</p>
                      </div>
                      <StatusPill label={step.status.replaceAll("_", " ")} tone={step.status === "FAILED" ? "danger" : step.status === "COMPLETED" ? "success" : step.status === "READY" ? "info" : "neutral"} />
                    </div>
                    {artifact ? <div className="execution-mini-result">{renderArtifact(artifact)}</div> : <div className="workflow-artifact-empty">{step.status === "LOCKED" ? "Waiting for agent." : step.status === "RUNNING" ? "Agent is running…" : "Ready to run."}</div>}
                    {isCurrent && step.status !== "COMPLETED" && (
                      <div className="execution-next-holder">
                        <div>
                          <span className="eyebrow">NEXT OPERATION</span>
                          <strong>{stepCanRun ? executionActionLabel(step.key) : "Agent is running…"}</strong>
                          <p>{stepCanRun ? "Ready when you are." : "Please wait."}</p>
                        </div>
                        {stepCanRun && <button className="button primary" disabled={!canRun} onClick={() => onStartStep(step.key)}>{executionActionLabel(step.key)} <span>→</span></button>}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="workflow-artifact">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">AGENT ARTIFACT</span>
                  <h3>{currentArtifact ? currentArtifact.type.replaceAll("_", " ") : "Waiting for output"}</h3>
                </div>
                {currentArtifact && <StatusPill label={currentArtifact.status} tone={currentArtifact.status === "APPROVED" ? "success" : currentArtifact.status === "REJECTED" ? "danger" : "info"} />}
              </div>
              {renderArtifact()}
            </div>
          )}
          {currentStep?.key === "REVIEW_RULES" && (
            <RulesPage
              proposals={proposals}
              configurations={configurations}
              profileReady
              busy={isRunning}
              canOperate={canOperate && !currentStep.temporary}
              onRequestProposals={() => undefined}
              onApprove={onApproveRule}
              onReject={onRejectRule}
              onEdit={onEditRule}
              onDelete={onDeleteRule}
              onSaveConfiguration={onSaveConfiguration}
              onCreateManual={onCreateManualRule}
              onRun={() => undefined}
              pipelineMode
            />
          )}
          {currentPhaseIndex !== 3 && <div className="workflow-actions">
            {currentStep &&
              ["READY", "FAILED", "COMPLETED"].includes(currentStep.status) &&
              !["UPLOAD_PROFILE"].includes(
                currentStep.key,
              ) && (
                <button
                  className="button primary"
                  disabled={
                    !canRun ||
                    (currentStep.key === "REVIEW_RULES" &&
                      Boolean(currentArtifact))
                  }
                  onClick={() => onStartStep(currentStep.key)}
                >
                  {currentStep.status === "COMPLETED"
                    ? `Re-run ${workflowStepLabels[currentStep.key].label}`
                    : nextActionLabel}
                </button>
              )}
            {reviewable && !currentStep?.temporary && (
              <>
                <button
                  className="button primary"
                  disabled={
                    !canOperate ||
                    isRunning ||
                    (currentStep?.key === "REVIEW_RULES" && !rulesDecided)
                  }
                  onClick={() =>
                    onReviewArtifact(currentArtifact.id, { action: "approve" })
                  }
                >
                  Confirm stage and continue
                </button>
                {currentStep?.key === "REVIEW_RULES" && !rulesDecided && (
                  <span className="muted">
                    Decide every rule and keep at least one approved rule before
                    continuing.
                  </span>
                )}
              </>
            )}
            {!canOperate && (
              <span className="muted">Read-only role: review is disabled.</span>
            )}
          </div>}
        </section>
      </div>
    </div>
  );
}

function App() {
  const { t, language } = useI18n();
  const [authenticated, setAuthenticated] = useState(
    () =>
      sessionStorage.getItem("ridepulse.auth") === "true" &&
      Boolean(sessionStorage.getItem("ridepulse.role")),
  );
  const [role, setRole] = useState<UserRole>(
    () =>
      (sessionStorage.getItem("ridepulse.role") as UserRole | null) ?? "USER",
  );
  const [username, setUsername] = useState(
    () => sessionStorage.getItem("ridepulse.username") ?? "",
  );
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [view, setView] = useState<View>("overview");
  const [wizardStep, setWizardStep] = useState<number>(1);
  const [showAdmin, setShowAdmin] = useState<boolean>(false);
  const [showGraphs, setShowGraphs] = useState<boolean>(false);
  const [showDataExplorer, setShowDataExplorer] = useState<boolean>(false);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(
    () => sessionStorage.getItem("ridepulse.dataset") ?? null,
  );
  const [profile, setProfile] = useState<DatasetProfile | null>(null);
  const [datasetProfiles, setDatasetProfiles] = useState<
    Record<string, DatasetProfile>
  >({});
  const [proposals, setProposals] = useState<RuleProposal[]>([]);
  const [ruleConfigurations, setRuleConfigurations] = useState<
    RuleConfiguration[]
  >([]);
  const [adminUsers, setAdminUsers] = useState<UserAccount[]>([]);
  const [datasetAccess, setDatasetAccess] = useState<DatasetAccess[]>([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  // Panels the step-1 buttons open over the page. Reading a chart or the audit
  // trail must not cost you the step you were working in.
  const [stepOverlay, setStepOverlay] = useState<
    "catalog" | "observatory" | "audit" | null
  >(null);
  // Step 3 reveals its review queue only after the Steward asks for rules, so
  // the screen opens on the contract the rules will be derived from rather than
  // on forty rows of output.
  const [ruleQueueOpen, setRuleQueueOpen] = useState(false);
  const [bulkReviewBusy, setBulkReviewBusy] = useState(false);
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  // Counted directly rather than derived from the list length: the list is
  // capped at 50, so once it is full the length stops growing and a derived
  // unread count would silently stick.
  const [unreadNotifications, setUnreadNotifications] = useState(0);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [activeJobStartedAt, setActiveJobStartedAt] = useState<string | undefined>();
  const [workflowActionBusy, setWorkflowActionBusy] = useState(false);
  const [activeRun, setActiveRun] = useState<DqRun | null>(null);
  const [dqResults, setDqResults] = useState<DqResult[]>([]);
  const [dqAnomalies, setDqAnomalies] = useState<DqAnomaly[]>([]);
  const [qualityTrends, setQualityTrends] = useState<QualityTrendPoint[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowRun | null>(null);
  const [workflowArtifacts, setWorkflowArtifacts] = useState<AgentArtifact[]>(
    [],
  );
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [retryAction, setRetryAction] = useState<(() => void) | null>(null);
  const [editingProposal, setEditingProposal] = useState<RuleProposal | null>(
    null,
  );
  // Thẻ cấu hình nào đang mở trong hàng đợi duyệt ở bước 3. RulesPage giữ state
  // riêng cho bản của nó; hàng đợi độc lập cần state riêng ở cấp này.
  const [expandedConfiguration, setExpandedConfiguration] = useState<string | null>(
    null,
  );
  const [manualRuleOpen, setManualRuleOpen] = useState(false);
  // Graph observability. The catalog is static topology fetched once; node runs
  // are telemetry refreshed alongside the workspace and while a graph is live.
  const [graphCatalog, setGraphCatalog] = useState<GraphCatalog | null>(null);
  const [nodeRuns, setNodeRuns] = useState<NodeRun[]>([]);
  const workflowNodeRuns = useMemo(() => (workflow ? nodeRuns.filter((run) => run.workflow_run_id === workflow.id) : []), [nodeRuns, workflow]);
  const [graphLoading, setGraphLoading] = useState(false);

  const dataset = useMemo(
    () => datasets.find((item) => item.id === selectedDatasetId) ?? datasets[0],
    [datasets, selectedDatasetId],
  );
  const approvedRules = useMemo(
    () => proposals.filter((proposal) => proposal.status === "APPROVED"),
    [proposals],
  );
  const canOperate = role === "STEWARD" || role === "ADMIN";
  const canAdmin = role === "ADMIN";

  const refreshWorkspace = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextDatasets, nextAudit] = await Promise.all([
        api.listDatasets(),
        api.listAuditLogs(),
      ]);
      setDatasets(nextDatasets);
      setAuditLogs(nextAudit);
      const profileEntries = await Promise.all(
        nextDatasets
          .filter((item) => item.status === "PROFILE_READY")
          .map(
            async (item) => [item.id, await api.getProfile(item.id)] as const,
          ),
      );
      const nextProfiles = Object.fromEntries(
        profileEntries.filter((entry): entry is [string, DatasetProfile] =>
          Boolean(entry[1]),
        ),
      ) as Record<string, DatasetProfile>;
      setDatasetProfiles(nextProfiles);
      const rememberedDatasetId = sessionStorage.getItem("ridepulse.dataset");
      const nextDataset =
        nextDatasets.find((item) => item.id === rememberedDatasetId) ??
        nextDatasets[0];
      setSelectedDatasetId(nextDataset?.id ?? null);
      if (nextDataset)
        sessionStorage.setItem("ridepulse.dataset", nextDataset.id);
      if (nextDataset?.status === "PROFILE_READY") {
        const [nextProposals, nextConfigurations, latestRun, nextTrends] =
          await Promise.all([
            api.listProposals(nextDataset.id),
            api.listRuleConfigurations(nextDataset.id),
            api.getLatestDqRun(nextDataset.id),
            api.getQualityTrends(nextDataset.id),
          ]);
        const nextProfile = nextProfiles[nextDataset.id] ?? null;
        setProfile(nextProfile);
        setQualityTrends(nextTrends);
        setActiveRun(latestRun);
        if (latestRun?.status === "SUCCEEDED") {
          const [latestResults, latestAnomalies] = await Promise.all([
            api.getDqResults(latestRun.id),
            api.getDqAnomalies(latestRun.id),
          ]);
          setDqResults(latestResults);
          setDqAnomalies(latestAnomalies);
        }
      } else {
        setProfile(null);
        setProposals([]);
        setRuleConfigurations([]);
        setActiveRun(null);
        setDqResults([]);
        setDqAnomalies([]);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearApiSession();
        sessionStorage.removeItem("ridepulse.auth");
        sessionStorage.removeItem("ridepulse.role");
        sessionStorage.removeItem("ridepulse.username");
        setAuthenticated(false);
      }
      setError(getErrorMessage(err, "Unable to load workspace."));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshNodeRuns = useCallback(async () => {
    if (!selectedDatasetId) return;
    setGraphLoading(true);
    try {
      // Once a workflow exists, ask the API for that exact session. Dataset
      // filtering alone includes historical Graph 1A executions and does not
      // guarantee that the just-started node runs are returned first.
      setNodeRuns(await api.listNodeRuns({
        datasetId: selectedDatasetId,
        workflowRunId: workflow?.id,
        limit: 500,
      }));
    } catch {
      // Telemetry is supporting detail; a failure here must not blank the page.
      setNodeRuns([]);
    } finally {
      setGraphLoading(false);
    }
  }, [selectedDatasetId, workflow?.id]);

  const loadNodeDetail = useCallback((nodeRunId: string) => api.getNodeRun(nodeRunId), []);

  // Real sub-progress for the running job. Each job type drives one graph, and
  // node telemetry records every node as it finishes, so counting the nodes of
  // the newest graph run gives measured progress between the job's own coarse
  // stage boundaries.
  // Graph 1B refuses to run until the semantic contract is CONFIRMED. Surfacing
  // that here turns a 409 after the click into a precondition stated before it.
  const understandingArtifact =
    workflow && dataset && workflow.dataset_id === dataset.id
      ? workflowArtifactForStep(workflow, workflowArtifacts, "UNDERSTAND_DATA")
      : undefined;
  const contractConfirmed =
    understandingArtifact?.status === "CONFIRMED" || understandingArtifact?.status === "APPROVED";

  const activeJobNodeProgress = useMemo(() => {
    if (!activeJob) return undefined;
    const graphKey = jobGraphKey[activeJob.type];
    if (!graphKey) return undefined;
    const relevant = nodeRuns.filter((run) => run.graph_key === graphKey);
    if (relevant.length === 0) return undefined;
    // Older runs for the same dataset are still in the list; keep only the
    // newest graph run or the count would include historical executions.
    const newest = relevant.reduce((latest, run) =>
      parseApiTimestamp(run.started_at) > parseApiTimestamp(latest.started_at) ? run : latest,
    );
    const current = relevant.filter((run) => run.graph_run_id === newest.graph_run_id);
    const total = graphCatalog?.graphs.find((graph) => graph.key === graphKey)?.nodes.length ?? current.length;
    const done = current.filter((run) => run.status === "SUCCEEDED" || run.status === "SKIPPED").length;
    const inFlight = current.find((run) => run.status === "RUNNING");
    const nodeStartedAt = current.reduce<string | undefined>(
      (earliest, run) => {
        if (!run.started_at) return earliest;
        if (!earliest) return run.started_at;
        return parseApiTimestamp(run.started_at) < parseApiTimestamp(earliest) ? run.started_at : earliest;
      },
      undefined,
    );
    return {
      done,
      total,
      current: inFlight
        ? (language === "vi"
            ? graphCatalog?.graphs.find((g) => g.key === graphKey)?.nodes.find((n) => n.name === inFlight.node_name)?.label_vi
            : graphCatalog?.graphs.find((g) => g.key === graphKey)?.nodes.find((n) => n.name === inFlight.node_name)?.label_en)
          ?? inFlight.node_name
        : undefined,
      startedAt: activeJobStartedAt ?? nodeStartedAt,
    };
  }, [activeJob, activeJobStartedAt, nodeRuns, graphCatalog, language]);
  const loadStewardReport = useCallback((runId: string) => api.getStewardReport(runId), []);

  // Topology never changes at runtime, so fetch it once per session.
  useEffect(() => {
    if (!authenticated || graphCatalog) return;
    let cancelled = false;
    api
      .getGraphCatalog()
      .then((catalog) => {
        if (!cancelled) setGraphCatalog(catalog);
      })
      .catch(() => {
        if (!cancelled) setGraphCatalog(null);
      });
    return () => {
      cancelled = true;
    };
  }, [authenticated, graphCatalog]);

  useEffect(() => {
    if (!authenticated || !selectedDatasetId) return;
    void refreshNodeRuns();
  }, [authenticated, selectedDatasetId, refreshNodeRuns]);

  // While a node is mid-flight the graph view is the one place a user watches
  // for progress, so poll until nothing is running any more.
  useEffect(() => {
    const running = nodeRuns.some((run) => run.status === "RUNNING");
    if (!running && !activeJob) return;
    const timer = window.setInterval(() => void refreshNodeRuns(), 4000);
    return () => window.clearInterval(timer);
  }, [nodeRuns, activeJob, refreshNodeRuns]);

  const refreshAdmin = useCallback(async () => {
    if (!dataset || !canAdmin) return;
    setAdminLoading(true);
    try {
      const [users, access] = await Promise.all([
        api.listUsers(),
        api.listDatasetAccess(dataset.id),
      ]);
      setAdminUsers(users);
      setDatasetAccess(access);
    } catch (err) {
      setError(getErrorMessage(err, "Unable to load administration controls."));
    } finally {
      setAdminLoading(false);
    }
  }, [canAdmin, dataset]);

  async function selectDataset(datasetId: string) {
    sessionStorage.setItem("ridepulse.dataset", datasetId);
    setSelectedDatasetId(datasetId);
    setWorkflow(null);
    setWorkflowArtifacts([]);
    await refreshWorkspace();
    // Selecting only selects. Confirm it so the absence of any analysis running
    // reads as "done, your move" rather than as the click not registering.
    const chosen = datasets.find((item) => item.id === datasetId);
    setToast(
      language === "vi"
        ? `Đã chọn "${chosen?.name ?? datasetId}". Chọn Profile dataset để tiếp tục.`
        : `Selected "${chosen?.name ?? datasetId}". Choose Profile dataset to continue.`,
    );
  }

  useEffect(() => {
    if (authenticated) void refreshWorkspace();
  }, [authenticated, refreshWorkspace]);
  useEffect(() => {
    if (view === "admin") void refreshAdmin();
  }, [refreshAdmin, view]);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // Every message the app raises is recorded here rather than at each of the
  // ~20 call sites. Watching the rendered value means a future setToast is
  // captured automatically instead of quietly bypassing the bell.
  useEffect(() => {
    if (!toast) return;
    setNotifications((current) => [
      { id: `n-${Date.now()}-${current.length}`, kind: "success" as const, message: toast, at: new Date().toISOString() },
      ...current,
    ].slice(0, 50));
    setUnreadNotifications((count) => count + 1);
  }, [toast]);

  useEffect(() => {
    if (!error) return;
    setNotifications((current) => [
      { id: `n-${Date.now()}-${current.length}`, kind: "error" as const, message: error, at: new Date().toISOString() },
      ...current,
    ].slice(0, 50));
    setUnreadNotifications((count) => count + 1);
  }, [error]);

  async function handleLogin(loginUsername: string, password: string) {
    setLoginBusy(true);
    setLoginError("");
    try {
      const session = await api.createSession(loginUsername, password);
      sessionStorage.setItem("ridepulse.auth", "true");
      sessionStorage.setItem("ridepulse.role", session.role);
      sessionStorage.setItem("ridepulse.username", session.username);
      setRole(session.role);
      setUsername(session.username);
      setAuthenticated(true);
    } catch (err) {
      setLoginError(getErrorMessage(err, "Unable to start session."));
    } finally {
      setLoginBusy(false);
    }
  }

  async function handleLogout() {
    await api.deleteSession();
    sessionStorage.removeItem("ridepulse.auth");
    sessionStorage.removeItem("ridepulse.role");
    sessionStorage.removeItem("ridepulse.username");
    sessionStorage.removeItem("ridepulse.dataset");
    setAuthenticated(false);
  }

  async function pollJob(
    acceptedJob: CreateJobResponse,
    onComplete: () => Promise<void>,
    jobApi: typeof api = api,
  ) {
    let current = await jobApi.getJob(acceptedJob.job_id);
    setActiveJob(current);
    for (
      let attempt = 0;
      attempt < 600 &&
      !["SUCCEEDED", "FAILED", "FAILED_RETRYABLE"].includes(current.status);
      attempt += 1
    ) {
      await sleep(1000);
      current = await jobApi.getJob(acceptedJob.job_id);
      setActiveJob(current);
    }
    const finalStatus = current.status as Job["status"];
    // The job is over either way, so the in-flight marker has to be cleared on
    // both paths. Leaving it set on failure disabled every run button on every
    // step — `activeJob` gates them all, and several handlers return early on
    // it — so one failed job bricked the page until a reload. The retry action
    // below is what lets the user try again, not a lingering activeJob.
    setActiveJob(null);
    setActiveJobStartedAt(undefined);
    if (finalStatus === "SUCCEEDED") {
      await onComplete();
      setRetryAction(null);
      setToast(language === "vi" ? "Tác vụ đã hoàn thành thành công." : "Job completed successfully.");
    } else {
      setRetryAction(() => () => void pollJob(acceptedJob, onComplete, jobApi));
      setError(
        current.error ??
          (finalStatus === "FAILED" || finalStatus === "FAILED_RETRYABLE"
            ? (language === "vi" ? "Tác vụ thất bại. Vui lòng thử lại khi sẵn sàng." : "The job failed. Retry the operation when ready.")
            : (language === "vi" ? "Tác vụ vẫn đang chạy sau 10 phút. Thử lại để tiếp tục theo dõi." : "The job is still running after 10 minutes. Retry to keep watching it.")),
      );
    }
  }

  async function startAnalysis() {
    if (!dataset) return;
    setError("");
    setRetryAction(null);
    try {
      const job = await api.startIngestion(dataset.id, crypto.randomUUID());
      await pollJob(job, async () => {
        setDatasets(await api.listDatasets());
        const nextProfile = await api.getProfile(dataset.id);
        setProfile(nextProfile);
        if (nextProfile)
          setDatasetProfiles((current) => ({
            ...current,
            [dataset.id]: nextProfile,
          }));
      });
    } catch (err) {
      setError(getErrorMessage(err, language === "vi" ? "Không thể phân tích hồ sơ dữ liệu." : "Unable to start analysis.", language));
    }
  }

  async function importDataset(file: File) {
    if (!canOperate || activeJob) return;
    setError("");
    setRetryAction(null);
    try {
      const imported = await api.importDataset(file);
      sessionStorage.setItem("ridepulse.dataset", imported.dataset.id);
      setSelectedDatasetId(imported.dataset.id);
      setDatasets((current) => [imported.dataset, ...current]);
      setView("datasets");
      if (imported.idempotent_replay) {
        // Dataset versions are content-addressed, so byte-identical bytes reuse
        // the existing version rather than profiling again. Say so: silence
        // here looks like the upload was ignored.
        setToast(
          language === "vi"
            ? `Tệp này trùng khớp hoàn toàn với "${imported.dataset.name}" đã có — dùng lại bản profile cũ, không chạy lại.`
            : `This file is byte-identical to "${imported.dataset.name}" — the existing profile was reused, nothing was re-run.`,
        );
      }
      await pollJob(imported.job, async () => {
        await refreshWorkspace();
      });
    } catch (err) {
      setError(getErrorMessage(err, language === "vi" ? "Không thể nạp bộ dữ liệu." : "Unable to import dataset.", language));
    }
  }

  async function deleteDataset(id: string) {
    if (!window.confirm(language === "vi" ? "Bạn có chắc chắn muốn xoá bộ dữ liệu này không?" : "Are you sure you want to delete this dataset?")) return;
    setDatasets((current) => current.filter((d) => d.id !== id));
    if (selectedDatasetId === id) {
      const remaining = datasets.filter((d) => d.id !== id);
      const nextId = remaining[0]?.id ?? "";
      setSelectedDatasetId(nextId);
      if (nextId) sessionStorage.setItem("ridepulse.dataset", nextId);
      else sessionStorage.removeItem("ridepulse.dataset");
    }
    setToast(language === "vi" ? "Đã xoá bộ dữ liệu khỏi không gian làm việc." : "Dataset removed from workspace.");
  }

  async function requestProposals() {
    if (!dataset) return;
    setError("");
    setRetryAction(null);
    setProposals([]);
    try {
      if (workflow) {
        await startWorkflowStep("PROPOSE_RULES", true);
      } else {
        const job = await api.startRuleProposals(dataset.id, crypto.randomUUID());
        await pollJob(job, async () => {
          setProposals(await api.listProposals(dataset.id));
          setAuditLogs(await api.listAuditLogs());
          setView("rules");
        });
      }
    } catch (err) {
      setError(getErrorMessage(err, "Unable to request proposals."));
    }
  }

  async function reviewProposal(id: string, action: "approve" | "reject") {
    setError("");
    try {
      await api.reviewProposal(id, { action });
      setProposals(await api.listProposals(dataset.id, workflow?.id));
      setRuleConfigurations(await api.listRuleConfigurations(dataset.id));
      setAuditLogs(await api.listAuditLogs());
      setToast(
        action === "approve"
          ? "Rule approved for execution."
          : "Proposal rejected and kept out of execution.",
      );
      setError("");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to update proposal."));
    }
  }

  async function deleteProposal(id: string) {
    if (!dataset) return;
    setError("");
    try {
      await api.deleteProposal(id);
      setProposals(await api.listProposals(dataset.id, workflow?.id));
      setRuleConfigurations(await api.listRuleConfigurations(dataset.id));
      setAuditLogs(await api.listAuditLogs());
      setToast("Proposal removed. Audit history was retained.");
      setError("");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to delete proposal."));
    }
  }

  async function saveRuleConfiguration(
    id: string,
    input: RuleConfigurationInput,
  ) {
    if (!dataset) return;
    try {
      await api.updateRuleConfiguration(id, input);
      setRuleConfigurations(await api.listRuleConfigurations(dataset.id));
      setAuditLogs(await api.listAuditLogs());
      setToast("Execution settings saved.");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to update rule settings."));
    }
  }

  async function createAdminUser(input: UserCreateInput) {
    try {
      await api.createUser(input);
      await refreshAdmin();
      setToast(`Account '${input.username}' created.`);
    } catch (err) {
      setError(getErrorMessage(err, "Unable to create account."));
    }
  }

  async function updateAdminUser(username: string, input: UserUpdateInput) {
    try {
      await api.updateUser(username, input);
      await refreshAdmin();
      setToast(`Account '${username}' updated.`);
    } catch (err) {
      setError(getErrorMessage(err, "Unable to update account."));
    }
  }

  async function grantAdminAccess(
    username: string,
    accessLevel: DatasetAccessLevel,
  ) {
    if (!dataset) return;
    try {
      await api.grantDatasetAccess(dataset.id, username, accessLevel);
      await refreshAdmin();
      setToast(`Dataset access updated for '${username}'.`);
    } catch (err) {
      setError(getErrorMessage(err, "Unable to grant dataset access."));
    }
  }

  async function revokeAdminAccess(username: string) {
    if (!dataset) return;
    try {
      await api.revokeDatasetAccess(dataset.id, username);
      await refreshAdmin();
      setToast(`Dataset access revoked for '${username}'.`);
    } catch (err) {
      setError(getErrorMessage(err, "Unable to revoke dataset access."));
    }
  }

  async function saveEdit(input: {
    title: string;
    description: string;
    severity: RuleProposal["severity"];
    rule: RuleSpec;
  }) {
    if (!editingProposal) return;
    try {
      await api.reviewProposal(editingProposal.id, {
        action: "edit",
        ...input,
      });
      setProposals(await api.listProposals(dataset.id, workflow?.id));
      setAuditLogs(await api.listAuditLogs());
      setEditingProposal(null);
      setToast("Proposal edited and marked ready for approval.");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to edit proposal."));
    }
  }

  async function createManualRule(input: ManualRuleInput) {
    if (!dataset) return;
    try {
      await api.createManualRule(dataset.id, input);
      setProposals(await api.listProposals(dataset.id, workflow?.id));
      setAuditLogs(await api.listAuditLogs());
      setManualRuleOpen(false);
      setToast("Manual rule created and queued for approval.");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to create manual rule."));
    }
  }

  async function runApprovedRules() {
    try {
      // Pausing a rule means "do not execute this one". The endpoint refuses the
      // whole request if any named rule is paused, so sending them all made one
      // paused rule fail the entire run instead of being skipped.
      const pausedIds = new Set(
        ruleConfigurations
          .filter((configuration) => configuration.execution_status === "PAUSED")
          .map((configuration) => configuration.rule_id),
      );
      const runnable = approvedRules.filter(
        (rule) => !pausedIds.has(rule.id) && !pausedIds.has(rule.id.replace(/^rv_/, "")),
      );
      const skipped = approvedRules.length - runnable.length;
      if (runnable.length === 0) {
        setError(
          language === "vi"
            ? "Mọi luật đã duyệt đang tạm dừng. Bật lại ít nhất một luật ở Execution settings trước khi chạy."
            : "Every approved rule is paused. Resume at least one in Execution settings before running.",
        );
        return;
      }
      if (skipped > 0) {
        setToast(
          language === "vi"
            ? `Bỏ qua ${skipped} luật đang tạm dừng.`
            : `Skipped ${skipped} paused rule${skipped === 1 ? "" : "s"}.`,
        );
      }
      const queuedRun = await api.startDqRun(
        runnable.map((rule) => rule.id),
        crypto.randomUUID(),
      );
      setActiveRun(await api.getDqRun(queuedRun.run_id));
      await pollJob(
        { job_id: queuedRun.job_id, status: queuedRun.status },
        async () => {
          const completed = await api.getDqRun(queuedRun.run_id);
          setActiveRun(completed);
          const [nextResults, nextAnomalies] = await Promise.all([
            api.getDqResults(queuedRun.run_id),
            api.getDqAnomalies(queuedRun.run_id),
          ]);
          setDqResults(nextResults);
          setDqAnomalies(nextAnomalies);
          if (dataset) setQualityTrends(await api.getQualityTrends(dataset.id));
          setAuditLogs(await api.listAuditLogs());
          setView("runs");
        },
      );
    } catch (err) {
      setError(getErrorMessage(err, "Unable to start DQ run."));
    }
  }

  async function refreshWorkflow(workflowId: string) {
    const [nextWorkflow, nextArtifacts] = await Promise.all([
      workflowApi.getWorkflow(workflowId),
      workflowApi.listWorkflowArtifacts(workflowId),
    ]);
    setWorkflow(nextWorkflow);
    setWorkflowArtifacts(nextArtifacts);
  }

  async function startWorkflowStep(step: WorkflowStepKey, fresh = false) {
    if (!dataset || !canOperate || workflowActionBusy || activeJob) return;
    setError("");
    setRetryAction(null);
    setWorkflowActionBusy(true);
    setActiveJobStartedAt(new Date().toISOString());
    try {
      if (!workflow && step === "UPLOAD_PROFILE") {
        const ingestion = await api.startIngestion(
          dataset.id,
          crypto.randomUUID(),
        );
        await pollJob(ingestion, async () => {
          const [nextDatasets, currentWorkflow] = await Promise.all([
            api.listDatasets(),
            workflowApi.createWorkflow(dataset.id, true),
          ]);
          setDatasets(nextDatasets);
          setProfile(await api.getProfile(dataset.id));
          setWorkflow(currentWorkflow);
          setWorkflowArtifacts(
            await workflowApi.listWorkflowArtifacts(currentWorkflow.id),
          );
          setAuditLogs(await api.listAuditLogs());
        });
        return;
      }
      let currentWorkflow = workflow;
      if (!currentWorkflow) {
        // Selecting a dataset clears the workflow, so the first click on a run
        // button lands here. This used to create the workflow and return, which
        // meant the button had to be pressed twice: the first press looked like
        // it had done nothing. Every caller of this function is an explicit run
        // request, so create the workflow and then carry out what was asked.
        currentWorkflow = await workflowApi.createWorkflow(dataset.id, fresh);
        setWorkflow(currentWorkflow);
        setWorkflowArtifacts(
          await workflowApi.listWorkflowArtifacts(currentWorkflow.id),
        );
        setProposals(await api.listProposals(dataset.id, currentWorkflow.id));
      }
      const queuedJob = await workflowApi.runWorkflowStep(
        currentWorkflow.id,
        step,
      );
      await pollJob(
        queuedJob,
        async () => {
          await refreshWorkflow(currentWorkflow!.id);
          await refreshNodeRuns();
          setProfile(await api.getProfile(dataset.id));
          setProposals(
            await api.listProposals(dataset.id, currentWorkflow!.id),
          );
          setAuditLogs(await api.listAuditLogs());
        },
        workflowApi,
      );
    } catch (err) {
      setError(getErrorMessage(err, "Unable to run workflow step."));
    } finally {
      setWorkflowActionBusy(false);
    }
  }

  async function confirmSemanticContract(artifact: AgentArtifact) {
    if (!workflow || !canOperate || workflowActionBusy || activeJob) return;
    setError("");
    setWorkflowActionBusy(true);
    try {
      const result = await workflowApi.confirmSemanticContract(workflow.id, {
        artifact_id: artifact.id,
        expected_version: artifact.version,
        // Confirming without edits sends the contract back unchanged; the
        // endpoint treats the body as the authoritative version.
        contract: (artifact.payload ?? {}) as Record<string, unknown>,
      });
      setWorkflow(result.workflow);
      setWorkflowArtifacts(await workflowApi.listWorkflowArtifacts(result.workflow.id));
      setAuditLogs(await api.listAuditLogs());
      setToast(
        language === "vi"
          ? "Đã xác nhận hợp đồng ngữ nghĩa."
          : "Semantic contract confirmed.",
      );
    } catch (err) {
      setError(getErrorMessage(err, "Unable to confirm the semantic contract."));
    } finally {
      setWorkflowActionBusy(false);
    }
  }

  async function bulkReviewProposals(action: "approve" | "reject") {
    if (!dataset || !canOperate || bulkReviewBusy) return;
    const pending = proposals.filter((item) => ["PROPOSED", "EDITED"].includes(item.status));
    if (pending.length === 0) {
      setToast(
        language === "vi"
          ? "Không còn đề xuất nào đang chờ quyết định."
          : "No proposals are awaiting a decision.",
      );
      return;
    }
    // Deciding dozens of rules at once is hard to undo, so it is confirmed.
    const question =
      language === "vi"
        ? `${action === "approve" ? "Duyệt" : "Từ chối"} toàn bộ ${pending.length} đề xuất đang chờ?`
        : `${action === "approve" ? "Approve" : "Reject"} all ${pending.length} pending proposals?`;
    if (!window.confirm(question)) return;

    setError("");
    setBulkReviewBusy(true);
    try {
      const updated = await api.bulkReviewProposals({
        dataset_id: dataset.id,
        action,
        pending_only: true,
      });
      setProposals(updated);
      setRuleConfigurations(await api.listRuleConfigurations(dataset.id));
      setAuditLogs(await api.listAuditLogs());
      setToast(
        language === "vi"
          ? `Đã ${action === "approve" ? "duyệt" : "từ chối"} ${pending.length} đề xuất.`
          : `${action === "approve" ? "Approved" : "Rejected"} ${pending.length} proposals.`,
      );
    } catch (err) {
      setError(getErrorMessage(err, "Unable to apply the bulk decision."));
    } finally {
      setBulkReviewBusy(false);
    }
  }

  async function advanceWorkflowStep() {
    if (!workflow || !canOperate || activeJob || workflowActionBusy) return;
    try {
      const nextWorkflow = await workflowApi.advanceWorkflowStep(workflow.id);
      setWorkflow(nextWorkflow);
      setToast(
        `Moved to ${workflowStepLabels[nextWorkflow.current_step].label}.`,
      );
    } catch (err) {
      setError(
        getErrorMessage(err, "Unable to move to the next workflow step."),
      );
    }
  }

  async function navigateForwardWorkflowStep() {
    if (!workflow || !canOperate || activeJob || workflowActionBusy) return;
    const currentIndex = workflow.steps.findIndex(
      (step) => step.key === workflow.current_step,
    );
    const nextStep = workflow.steps[currentIndex + 1];
    if (nextStep?.temporary) {
      await rewindWorkflowStage(nextStep.key);
      return;
    }
    await advanceWorkflowStep();
  }

  async function reviewWorkflowArtifact(
    id: string,
    input: ArtifactReviewInput,
  ) {
    if (!canOperate || workflowActionBusy || activeJob) return;
    setError("");
    try {
      const updated = await workflowApi.reviewArtifact(id, input);
      setWorkflowArtifacts((current) =>
        current.map((artifact) => (artifact.id === id ? updated : artifact)),
      );
      if (workflow) await refreshWorkflow(workflow.id);
      setToast(
        input.action === "approve"
          ? "Artifact approved. The next workflow step is ready."
          : input.action === "reject"
            ? "Artifact rejected and kept out of execution."
            : "Revision requested from the agent.",
      );
      setError("");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to review workflow artifact."));
    }
  }

  async function decideWorkflowLoop(input: LoopDecisionInput) {
    if (!workflow || !canOperate) return;
    try {
      setWorkflow(await workflowApi.continueLoop(workflow.id, input));
      setWorkflowArtifacts(
        await workflowApi.listWorkflowArtifacts(workflow.id),
      );
      setAuditLogs(await api.listAuditLogs());
      setToast(
        input.action === "continue"
          ? "Loop continued with a bounded next iteration."
          : "Loop stopped by the steward.",
      );
    } catch (err) {
      setError(getErrorMessage(err, "Unable to update loop decision."));
    }
  }

  async function rewindWorkflowStage(targetStep: WorkflowStepKey) {
    if (!workflow || !canOperate || activeJob || workflowActionBusy) return;
    const label = workflowStepLabels[targetStep].label;
    try {
      const nextWorkflow = await workflowApi.rewindWorkflow(
        workflow.id,
        targetStep,
      );
      setWorkflow(nextWorkflow);
      setWorkflowArtifacts(
        await workflowApi.listWorkflowArtifacts(workflow.id),
      );
      setToast(
        `Returned to ${label}. Later stage sessions are kept temporarily until this stage changes.`,
      );
    } catch (err) {
      setError(getErrorMessage(err, "Unable to return to workflow stage."));
    }
  }

  if (!authenticated)
    return (
      <LoginScreen onLogin={handleLogin} busy={loginBusy} error={loginError} />
    );
  return (
    <div className="app-shell wizard-shell">
      <main className="main-content full-width">
        <header className="topbar">
          <div className="brand-lockup">
            <span className="brand-mark">RP</span>
            <span>
              RidePulse <em>DQ</em>
            </span>
          </div>
          <div className="topbar-actions">
            <span className="role-badge">{role}</span>
            <LanguageToggle />
            <ThemeControl />
            <button
              type="button"
              className={`button secondary ${showGraphs ? "active" : ""}`}
              onClick={() => {
                setShowGraphs(!showGraphs);
                setShowAdmin(false);
              }}
            >
              ⛓ {t("app.graphObservatory")}
            </button>
            {canAdmin && (
              <button
                type="button"
                className={`button secondary ${showAdmin ? "active" : ""}`}
                onClick={() => setShowAdmin(!showAdmin)}
              >
                ⚙ {t("app.adminControl")}
              </button>
            )}
            <button
              type="button"
              className={`notif-button ${stepOverlay === "audit" ? "active" : ""}`}
              onClick={() => setStepOverlay(stepOverlay === "audit" ? null : "audit")}
              aria-label={language === "vi" ? "Nhật ký kiểm toán" : "Audit log"}
              title={language === "vi" ? "Nhật ký kiểm toán" : "Audit log"}
            >
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
                <path
                  d="M7 3.5h7.5L18 7v13.5H7z"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                  strokeLinejoin="round"
                />
                <path d="M14 3.5V7h4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                <path d="M9.5 11h6M9.5 14h6M9.5 17h3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
            </button>
            <NotificationBell
              notifications={notifications}
              unreadCount={unreadNotifications}
              language={language}
              onOpen={() => setUnreadNotifications(0)}
              onClear={() => {
                setNotifications([]);
                setUnreadNotifications(0);
              }}
            />
            <button
              type="button"
              className="profile-button-mini icon-button"
              onClick={() => void handleLogout()}
              title={t("app.signOut")}
            >
              <span className="avatar mini">
                {role === "ADMIN" ? "AD" : role === "STEWARD" ? "DS" : "US"}
              </span>
            </button>
          </div>
        </header>

        {!showAdmin && !showGraphs && !stepOverlay && (
          <div className="wizard-header-container">
            <nav className="wizard-stepper" aria-label="Wizard Steps">
              {[
                {
                  id: 1,
                  title: t("wizard.step1Title"),
                  desc: t("wizard.step1Desc"),
                },
                {
                  id: 2,
                  title: t("wizard.step2Title"),
                  desc: t("wizard.step2Desc"),
                },
                {
                  id: 3,
                  title: t("wizard.step3Title"),
                  desc: t("wizard.step3Desc"),
                },
                {
                  id: 4,
                  title: t("wizard.step4Title"),
                  desc: t("wizard.step4Desc"),
                },
                {
                  id: 5,
                  title: t("wizard.step5Title"),
                  desc: t("wizard.step5Desc"),
                },
              ].map((step, idx) => (
                <div key={step.id} style={{ display: "contents" }}>
                  {idx > 0 && (
                    <div
                      className={`wizard-connector ${wizardStep >= step.id ? "filled" : ""}`}
                    />
                  )}
                  <button
                    type="button"
                    className={`wizard-step-node ${
                      wizardStep === step.id
                        ? "active"
                        : wizardStep > step.id
                          ? "completed"
                          : ""
                    }`}
                    onClick={() => {
                      setShowAdmin(false);
                      setWizardStep(step.id);
                    }}
                  >
                    <div className="wizard-step-badge">
                      {wizardStep > step.id ? "✓" : step.id}
                    </div>
                    <div className="wizard-step-info">
                      <span className="wizard-step-title">{step.title}</span>
                      <span className="wizard-step-desc">{step.desc}</span>
                    </div>
                  </button>
                </div>
              ))}
            </nav>
          </div>
        )}

        <div className="page-container">
          {!canOperate && (
            <div className="dev-banner">
              <span>Read-only access</span>
              <span>
                Your role can inspect evidence and results but cannot change
                rules or start jobs.
              </span>
              <code>{role}</code>
            </div>
          )}
          {isMockMode && (
            <div className="dev-banner">
              <span>Local development adapter</span>
              <span>
                Results are deterministic fixtures until the Gate 2 backend is
                connected.
              </span>
              <code>VITE_USE_MOCK_API=false</code>
            </div>
          )}
          {error && (
            <div className="alert error">
              <strong>{language === "vi" ? "Thao tác thất bại" : "Action failed"}</strong>
              <span>{error}</span>
              {/* pollJob has always recorded how to retry a failed job, but
                  nothing rendered it: the message said "retry the operation"
                  while offering no way to do so. */}
              {retryAction && (
                <button
                  className="alert-retry"
                  onClick={() => {
                    const retry = retryAction;
                    setError("");
                    setRetryAction(null);
                    retry();
                  }}
                >
                  {language === "vi" ? "↻ Thử lại" : "↻ Retry"}
                </button>
              )}
              <button onClick={() => setError("")}>×</button>
            </div>
          )}
          {(toast || activeJob) && (
            <div className="floating-toasts-stack">
              {activeJob && (
                <ProgressPanel
                  job={activeJob}
                  title={
                    activeJob.type === "INGEST_PROFILE" || activeJob.type === "UNDERSTAND_DATA"
                      ? (language === "vi" ? "Đang nạp và phân tích hồ sơ dữ liệu…" : "Building dataset profile")
                      : activeJob.type === "PROPOSE_RULES"
                        ? (language === "vi" ? "Đang sinh đề xuất quy tắc…" : "Generating rule proposals")
                        : activeJob.type === "RUN_DQ" &&
                          /ANALYZE_REPORT|analysis report/i.test(activeJob.message)
                          ? (language === "vi" ? "Đang phân tích và tạo báo cáo…" : "Analyzing results and building report")
                          : (language === "vi" ? "Đang chạy kiểm thử quy tắc…" : "Running approved checks")
                  }
                  nodeProgress={activeJobNodeProgress}
                />
              )}
              {toast && (
                <div className="toast-notification">
                  <span className="toast-icon">✅</span>
                  <div className="toast-content">
                    <span className="toast-title">
                      {language === "vi" ? "Thông báo" : "Notification"}
                    </span>
                    <span className="toast-message">{toast}</span>
                  </div>
                  <button
                    className="toast-close"
                    onClick={() => setToast("")}
                    title={language === "vi" ? "Đóng" : "Close"}
                  >
                    ×
                  </button>
                </div>
              )}
            </div>
          )}
          {showGraphs ? (
            <GraphStagePanel
              catalog={graphCatalog}
              runs={workflowNodeRuns}
              graphKeys={["G1A", "G1B", "G2", "G3"]}
              language={language}
              loadNodeDetail={loadNodeDetail}
            />
          ) : showAdmin && canAdmin ? (
            <AdminPage
              users={adminUsers}
              access={datasetAccess}
              loading={adminLoading}
              onCreate={createAdminUser}
              onUpdate={updateAdminUser}
              onGrant={grantAdminAccess}
              onRevoke={revokeAdminAccess}
            />
          ) : (
            <>
              {/* STEP 1: Dataset preparation — import, profile, preview.
                  Everything about the dataset itself lives here so the four
                  graph steps that follow each hold exactly one graph. */}
              {wizardStep === 1 && (
                <Step1DataPreparation
                  datasets={datasets}
                  dataset={dataset}
                  language={language}
                  canOperate={canOperate}
                  importing={Boolean(activeJob)}
                  profiling={Boolean(activeJob)}
                  profileReady={Boolean(profile)}
                  onImportDataset={(file) => void importDataset(file)}
                  onSelectDataset={(id) => void selectDataset(id)}
                  onDeleteDataset={(id) => void deleteDataset(id)}
                  onOpenExplorer={(datasetId) => {
                    if (datasetId !== dataset?.id) void selectDataset(datasetId);
                    setShowDataExplorer(true);
                  }}
                  onProfileDataset={() => void startAnalysis()}
                  loadDictionary={(datasetId) => api.getDataDictionary(datasetId)}
                  uploadDictionary={(datasetId, file) => api.uploadDataDictionary(datasetId, file)}
                  deleteDictionary={(datasetId) => api.deleteDataDictionary(datasetId)}
                  profilePanel={
                    <OverviewPage
                      dataset={dataset}
                      /* Scoped to the selected dataset: the catalogue used to
                         list every dataset here, so the panel under one
                         dataset's name showed another dataset's numbers. */
                      datasets={dataset ? [dataset] : []}
                      profile={profile}
                      datasetProfiles={datasetProfiles}
                      qualityTrends={qualityTrends}
                      proposals={proposals}
                      approvedRules={approvedRules.length}
                      loading={loading}
                      busy={Boolean(activeJob)}
                      canOperate={canOperate}
                      onStartAnalysis={() => void startAnalysis()}
                      onRequestProposals={() => void requestProposals()}
                      onNavigate={(v) => {
                        // "datasets" and "rules" are real pipeline moves; the
                        // read-only views open in place so step 1 is not lost.
                        if (v === "datasets") setStepOverlay("catalog");
                        if (v === "visualization") setStepOverlay("observatory");
                        if (v === "audit") setStepOverlay("audit");
                        if (v === "rules") setWizardStep(3);
                      }}
                      onSelectDataset={(id) => void selectDataset(id)}
                    />
                  }
                  observatoryPanel={
                    <VisualizationPage
                      profile={profile}
                      results={dqResults}
                      anomalies={dqAnomalies}
                      trends={qualityTrends}
                    />
                  }
                />
              )}

              {/* STEP 2: Graph 1A — dataset understanding */}
              {wizardStep === 2 && (
                <div>
                  <div className="page-heading">
                    <div>
                      <span className="eyebrow">RUN 1 · {t("wizard.step2Title").toUpperCase()}</span>
                      <h1>{t("wizard.step2Title")}</h1>
                      <p>{t("wizard.step2Desc")}</p>
                    </div>
                  </div>
                  <GraphStagePanel
                    catalog={graphCatalog}
                    runs={workflowNodeRuns}
                    graphKeys={["G1A"]}
                    language={language}
                    loadNodeDetail={loadNodeDetail}
                  />
                  <div style={{ marginTop: "24px" }}>
                    <SemanticContractPanel
                      workflow={workflow}
                      artifacts={workflowArtifacts}
                      dataset={dataset}
                      profile={profile}
                      canOperate={canOperate}
                      busy={workflowActionBusy || Boolean(activeJob)}
                      onStartUnderstand={(id) => {
                        if (id !== dataset?.id) void selectDataset(id);
                        void startWorkflowStep("UNDERSTAND_DATA", true);
                      }}
                      onConfirmContract={(artifact) => void confirmSemanticContract(artifact)}
                      language={language}
                    />
                  </div>
                </div>
              )}

              {/* STEP 3: Rule Engineering */}
              {wizardStep === 3 && (
                <div>
                  <WorkflowPage
                    dataset={dataset}
                    profile={profile}
                    datasets={datasets}
                    workflow={workflow}
                    artifacts={workflowArtifacts}
                    proposals={proposals}
                    configurations={ruleConfigurations}
                    activeJob={activeJob}
                    busy={workflowActionBusy}
                    canOperate={canOperate}
                    onStartStep={(step, fresh) =>
                      void startWorkflowStep(step, fresh)
                    }
                    onAdvanceStep={() => void navigateForwardWorkflowStep()}
                    onReviewArtifact={(id, input) =>
                      void reviewWorkflowArtifact(id, input)
                    }
                    onLoopDecision={(input) => void decideWorkflowLoop(input)}
                    onApproveRule={(id) => void reviewProposal(id, "approve")}
                    onRejectRule={(id) => void reviewProposal(id, "reject")}
                    onEditRule={setEditingProposal}
                    onDeleteRule={(id) => void deleteProposal(id)}
                    onSaveConfiguration={(id, input) =>
                      void saveRuleConfiguration(id, input)
                    }
                    onCreateManualRule={() => setManualRuleOpen(true)}
                    onRewindStep={(step) => void rewindWorkflowStage(step)}
                    onSelectDataset={(id) => void selectDataset(id)}
                    onUploadPreview={(file) => void importDataset(file)}
                    onBackToDatasetSelection={() => setWizardStep(1)}
                    nodeProgress={activeJobNodeProgress}
                    graphPanel={
                      <GraphStagePanel
                        catalog={graphCatalog}
                        runs={workflowNodeRuns}
                        graphKeys={["G1B"]}
                        language={language}
                        loadNodeDetail={loadNodeDetail}
                      />
                    }
                  />
                  {/* Hàng đợi duyệt trước đây chỉ hiện khi workflow đi đúng tới
                      chặng REVIEW_RULES. Dataset có thể đã có sẵn đề xuất từ lần
                      chạy trước mà workflow lại chưa khởi tạo — khi đó người dùng
                      mở bước 3 và thấy màn hình trống, dù luật vẫn nằm trong DB.
                      Có đề xuất thì hiện, không phụ thuộc trạng thái workflow. */}
                  {/* Generating rules is its own decision, so it gets its own
                      control. The queue below stays closed until it is asked
                      for: opening step 3 straight onto forty rows buried the
                      contract those rows were derived from. */}
                  <section className="prep-section rule-gate">
                    <header className="prep-section-head">
                      <span className="prep-section-index">2</span>
                      <div className="prep-section-title">
                        <h2>{language === "vi" ? "Sinh luật từ hợp đồng" : "Generate rules"}</h2>
                        <p>
                          {understandingArtifact && !contractConfirmed
                            ? language === "vi"
                              ? "Hợp đồng ngữ nghĩa chưa được xác nhận. Xác nhận trước rồi mới sinh được luật."
                              : "The semantic contract is not confirmed yet. Confirm it before generating rules."
                            : proposals.length > 0
                              ? language === "vi"
                                ? `Đã có ${proposals.length} đề xuất cho bộ dữ liệu này.`
                                : `${proposals.length} proposals already exist for this dataset.`
                              : language === "vi"
                                ? "Agent đọc hợp đồng ngữ nghĩa ở trên và đề xuất bộ luật kiểm tra."
                                : "The agent reads the contract above and proposes a rule set."}
                        </p>
                      </div>
                      <div className="rule-gate-actions">
                        {understandingArtifact && !contractConfirmed && (
                          <button
                            className="button secondary"
                            disabled={!canOperate || workflowActionBusy || Boolean(activeJob)}
                            onClick={() => void confirmSemanticContract(understandingArtifact)}
                          >
                            {language === "vi" ? "Xác nhận hợp đồng" : "Confirm contract"}
                          </button>
                        )}
                        {proposals.length > 0 && (
                          <button
                            className="button secondary"
                            onClick={() => setRuleQueueOpen((open) => !open)}
                          >
                            {ruleQueueOpen
                              ? language === "vi" ? "Ẩn hàng đợi" : "Hide queue"
                              : language === "vi" ? `Xem ${proposals.length} đề xuất` : `Review ${proposals.length} proposals`}
                          </button>
                        )}
                        <button
                          className="button primary"
                          disabled={
                            !canOperate ||
                            Boolean(activeJob) ||
                            workflowActionBusy ||
                            (Boolean(understandingArtifact) && !contractConfirmed)
                          }
                          title={
                            understandingArtifact && !contractConfirmed
                              ? language === "vi"
                                ? "Xác nhận hợp đồng ngữ nghĩa trước khi sinh luật."
                                : "Confirm the semantic contract before generating rules."
                              : undefined
                          }
                          onClick={() => {
                            setRuleQueueOpen(true);
                            void requestProposals();
                          }}
                        >
                          {activeJob
                            ? language === "vi" ? "Đang sinh luật…" : "Generating…"
                            : proposals.length > 0
                              ? language === "vi" ? "↻ Sinh lại luật" : "↻ Regenerate rules"
                              : language === "vi" ? "⚡ Sinh Rule" : "⚡ Generate rules"}
                        </button>
                      </div>
                    </header>
                  </section>

                  {proposals.length > 0 && ruleQueueOpen && (
                    <section className="standalone-review prep-section">
                      <header className="prep-section-head">
                        <span className="prep-section-index">3</span>
                        <div className="prep-section-title">
                          <h2>{language === "vi" ? "Duyệt đề xuất luật" : "Review rule proposals"}</h2>
                          <p>
                            {language === "vi"
                              ? `${proposals.length} đề xuất cho ${dataset?.name ?? "bộ dữ liệu này"}`
                              : `${proposals.length} proposals for ${dataset?.name ?? "this dataset"}`}
                          </p>
                        </div>
                        <div className="rule-gate-actions">
                          <button
                            className="button ghost danger"
                            disabled={!canOperate || bulkReviewBusy}
                            onClick={() => void bulkReviewProposals("reject")}
                          >
                            {language === "vi" ? "Từ chối tất cả" : "Reject all"}
                          </button>
                          <button
                            className="button primary"
                            disabled={!canOperate || bulkReviewBusy}
                            onClick={() => void bulkReviewProposals("approve")}
                          >
                            {language === "vi" ? "Duyệt tất cả" : "Approve all"}
                          </button>
                        </div>
                      </header>
                      <ReviewSummaryPanel proposals={proposals} />
                      <div className="proposal-list">
                        {proposals.map((proposal) => (
                          <ProposalCard
                            key={proposal.id}
                            proposal={proposal}
                            canOperate={canOperate}
                            configuration={ruleConfigurations.find(
                              (item) => item.rule_id === proposal.id,
                            )}
                            configurationExpanded={
                              expandedConfiguration === proposal.id
                            }
                            onToggleConfiguration={() =>
                              setExpandedConfiguration(
                                expandedConfiguration === proposal.id
                                  ? null
                                  : proposal.id,
                              )
                            }
                            onSaveConfiguration={(input) =>
                              void saveRuleConfiguration(proposal.id, input)
                            }
                            onApprove={() => void reviewProposal(proposal.id, "approve")}
                            onReject={() => void reviewProposal(proposal.id, "reject")}
                            onEdit={() => setEditingProposal(proposal)}
                            onDelete={() => void deleteProposal(proposal.id)}
                          />
                        ))}
                      </div>
                      {/* Repeated at the foot of the list: after scrolling forty
                          rules, the controls at the top are long gone. */}
                      <div className="bulk-review-bar">
                        <span>
                          {language === "vi"
                            ? `${proposals.filter((p) => ["PROPOSED", "EDITED"].includes(p.status)).length} đề xuất đang chờ quyết định`
                            : `${proposals.filter((p) => ["PROPOSED", "EDITED"].includes(p.status)).length} awaiting a decision`}
                        </span>
                        <div className="rule-gate-actions">
                          <button
                            className="button ghost danger"
                            disabled={!canOperate || bulkReviewBusy}
                            onClick={() => void bulkReviewProposals("reject")}
                          >
                            {language === "vi" ? "Từ chối tất cả" : "Reject all rules"}
                          </button>
                          <button
                            className="button primary"
                            disabled={!canOperate || bulkReviewBusy}
                            onClick={() => void bulkReviewProposals("approve")}
                          >
                            {language === "vi" ? "Duyệt tất cả" : "Approve all rules"}
                          </button>
                        </div>
                      </div>
                    </section>
                  )}
                </div>
              )}

              {/* STEP 4: Graph 2 — deterministic execution */}
              {wizardStep === 4 && (
                <div>
                  <RunsPage
                    activeRun={activeRun}
                    results={dqResults}
                    anomalies={dqAnomalies}
                    approvedCount={approvedRules.length}
                    busy={Boolean(activeJob)}
                    canOperate={canOperate}
                    datasetId={dataset?.id}
                    onRun={() => void runApprovedRules()}
                    graphPanel={
                      <>
                        <GraphStagePanel
                          catalog={graphCatalog}
                        runs={workflowNodeRuns}
                          /* G2_DIRECT is what the button on this step runs; the
                             dbt graph (G2) belongs to the analysis workflow and
                             is shown after it so both paths stay visible. */
                          graphKeys={["G2_DIRECT", "G2"]}
                          language={language}
                          loadNodeDetail={loadNodeDetail}
                        />
                        {/* Graph 3: ANOMALY_REPORT from workflow artifacts */}
                        {workflowArtifacts.filter((a) => a.type === "ANOMALY_REPORT").slice(-1).map((anomalyArtifact) => {
                          const p = anomalyArtifact.payload as Record<string, unknown>;
                          const dec = String(p.decision ?? "UNAVAILABLE");
                          const hyps = Array.isArray(p.hypotheses) ? (p.hypotheses as Record<string, unknown>[]) : [];
                          const tone: "danger" | "success" | "warning" = dec === "ANOMALY" || dec === "CRITICAL" ? "danger" : dec === "NORMAL" ? "success" : "warning";
                          return (
                            <div key={anomalyArtifact.id} style={{ marginTop: "24px", padding: "24px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "16px" }}>
                              <span className="eyebrow">GRAPH 3 — AI ANOMALY ANALYSIS</span>
                              <h3 style={{ fontSize: "16px", fontWeight: 700, margin: "8px 0 16px" }}>Steward Insights</h3>
                              <div style={{ display: "flex", gap: "12px", alignItems: "center", marginBottom: "16px", flexWrap: "wrap" }}>
                                <StatusPill label={dec === "INSUFFICIENT_HISTORY" ? "NOT ENOUGH HISTORY" : dec} tone={tone} />
                                {typeof p.score === "number" && <span style={{ fontSize: "13px", color: "var(--muted)" }}>Score: <strong>{(p.score as number).toFixed(1)}</strong></span>}
                                {typeof p.confidence === "number" && <span style={{ fontSize: "13px", color: "var(--muted)" }}>Confidence: <strong>{Math.round((p.confidence as number) * 100)}%</strong></span>}
                              </div>
                              {hyps.map((h, i) => (
                                <div key={i} style={{ padding: "10px 14px", background: "var(--surface-muted, #f8fafc)", borderRadius: "8px", border: "1px solid var(--border)", marginBottom: "8px" }}>
                                  <p style={{ margin: 0, fontWeight: 600, fontSize: "14px" }}>{String(h.summary ?? "No hypothesis.")}</p>
                                  {typeof h.confidence === "number" && <span style={{ fontSize: "12px", color: "var(--muted)" }}>Confidence: {Math.round((h.confidence as number) * 100)}%</span>}
                                </div>
                              ))}
                            </div>
                          );
                        })}
                      </>
                    }
                  />
                  {dataset && <ActiveRulesPanel datasetId={dataset.id} />}
                  {/* The audit history used to be pasted onto the bottom of this
                      step, pushing the results it was meant to annotate off the
                      screen. It now lives behind the topbar log button, which
                      opens it over whatever page you are on. */}
                </div>
              )}

              {/* STEP 5: Graph 3 — anomaly detection and root cause */}
              {wizardStep === 5 && (
                <div>
                  <div className="page-heading">
                    <div>
                      <span className="eyebrow">RUN 3 · {t("wizard.step5Title").toUpperCase()}</span>
                      <h1>{t("wizard.step5Title")}</h1>
                      <p>{t("wizard.step5Desc")}</p>
                    </div>
                    <button className="button secondary" onClick={() => setWizardStep(1)}>
                      {t("wizard.startNewRun")}
                    </button>
                  </div>
                  <GraphStagePanel
                    catalog={graphCatalog}
                    runs={workflowNodeRuns}
                    graphKeys={["G3"]}
                    language={language}
                    loadNodeDetail={loadNodeDetail}
                    emptyNote={
                      language === "vi"
                        ? "Các node này chạy trong luồng phân tích Graph 2 + Graph 3. Số liệu bên dưới đến từ bộ phát hiện bất thường chạy kèm nút \"Chạy luật đã duyệt\" ở bước 4, nên các node ở đây chưa được kích hoạt."
                        : "These nodes run in the Graph 2 + Graph 3 analysis workflow. The figures below come from the anomaly detection that runs alongside \"Run approved rules\" in step 4, so nothing here has been invoked yet."
                    }
                  />
                  {activeRun ? (
                    <>
                      <div style={{ marginTop: "24px" }}>
                        <StewardReportPanel
                          runId={activeRun.id}
                          language={language}
                          loadReport={loadStewardReport}
                        />
                      </div>
                      <div style={{ marginTop: "24px" }}>
                        <AnomalyInvestigationPanel runId={activeRun.id} canOperate={canOperate} />
                      </div>
                      <div style={{ marginTop: "32px" }}>
                        <AnomalyStatisticsPanel
                          anomalies={dqAnomalies}
                          results={dqResults}
                          language={language}
                        />
                      </div>
                    </>
                  ) : (
                    /* Graph 3 only has anything to say once Graph 2 has run.
                       Say that plainly instead of showing an empty screen. */
                    <section className="panel investigation-panel" style={{ marginTop: "24px" }}>
                      <div className="panel-heading">
                        <div>
                          <span className="eyebrow">ĐIỀU TRA NGUYÊN NHÂN GỐC</span>
                          <h3>Giả thuyết từ agent</h3>
                        </div>
                      </div>
                      <p className="investigation-note">
                        Chạy bộ luật đã duyệt ở bước 4 để agent phân tích kết quả.
                        Sau khi chạy, khu vực này hiện các giả thuyết nguyên nhân
                        gốc kèm bằng chứng ủng hộ, bằng chứng phản bác và những
                        kiểm tra được khuyến nghị.
                      </p>
                    </section>
                  )}
                </div>
              )}

              {/* Wizard Bottom Nav Controls */}
              <div className="wizard-footer-nav">
                <button
                  type="button"
                  className="button secondary"
                  disabled={wizardStep === 1}
                  onClick={() => setWizardStep((prev) => Math.max(1, prev - 1))}
                >
                  {t("wizard.back")}
                </button>

                <span className="muted" style={{ fontWeight: 600 }}>
                  {t("wizard.stepProgress", { current: wizardStep, total: 5 })}
                </span>

                <button
                  type="button"
                  className="button primary"
                  disabled={wizardStep === 5 || (!dataset && wizardStep === 1) || (wizardStep === 1 && !profile)}
                  title={
                    !dataset && wizardStep === 1
                      ? (t("wizard.selectDatasetTooltip") || "Vui lòng chọn hoặc tải lên một bộ dữ liệu ở Bước 1")
                      : wizardStep === 1 && !profile
                        ? (language === "vi" ? "Hãy tạo profile cho tập dữ liệu trước" : "Build the dataset profile first")
                        : ""
                  }
                  onClick={() => {
                    setWizardStep((prev) => Math.min(5, prev + 1));
                  }}
                >
                  {t("wizard.next")}
                </button>
              </div>
            </>
          )}
      {stepOverlay && (
        <DetailOverlay
          eyebrow={
            stepOverlay === "catalog"
              ? language === "vi" ? "TOÀN BỘ DANH MỤC" : "FULL CATALOGUE"
              : stepOverlay === "observatory"
                ? language === "vi" ? "PHÒNG ĐIỀU KHIỂN CHẤT LƯỢNG" : "QUALITY CONTROL ROOM"
                : language === "vi" ? "LỊCH SỬ CHỈ GHI THÊM" : "APPEND-ONLY HISTORY"
          }
          title={
            stepOverlay === "catalog"
              ? language === "vi" ? "Danh mục bộ dữ liệu" : "Dataset catalog"
              : stepOverlay === "observatory"
                ? language === "vi" ? "Quan sát chất lượng dữ liệu" : "Data quality observatory"
                : language === "vi" ? "Lịch sử kiểm toán" : "Audit history"
          }
          closeLabel={language === "vi" ? "Đóng" : "Close"}
          onClose={() => setStepOverlay(null)}
        >
          {stepOverlay === "catalog" && (
            <DatasetCatalogView
              datasets={datasets}
              datasetProfiles={datasetProfiles}
              selectedId={dataset?.id}
              language={language}
              onSelectDataset={(id) => {
                void selectDataset(id);
                setStepOverlay(null);
              }}
            />
          )}
          {stepOverlay === "observatory" && (
            <VisualizationPage
              profile={profile}
              results={dqResults}
              anomalies={dqAnomalies}
              trends={qualityTrends}
            />
          )}
          {stepOverlay === "audit" && <AuditPage logs={auditLogs} />}
        </DetailOverlay>
      )}
      {showDataExplorer && dataset && (
        <DataExplorerDialog
          dataset={dataset}
          language={language}
          loadRows={(datasetId, limit) =>
            api.queryDatasetRows(datasetId, { limit, offset: 0, quality_status: "ALL" })
          }
          loadDictionary={(datasetId) => api.getDataDictionary(datasetId)}
          onClose={() => setShowDataExplorer(false)}
        />
      )}
      {editingProposal && (
        <EditDialog
          proposal={editingProposal}
          onClose={() => setEditingProposal(null)}
          onSave={(input) => void saveEdit(input)}
        />
      )}
      {manualRuleOpen && (
        <ManualRuleDialog
          onClose={() => setManualRuleOpen(false)}
          onSave={(input) => void createManualRule(input)}
        />
      )}
        </div>
      </main>
    </div>
  );
}

function OverviewPage({
  dataset,
  datasets,
  profile,
  datasetProfiles,
  qualityTrends,
  proposals,
  approvedRules,
  loading,
  busy,
  canOperate,
  onStartAnalysis,
  onRequestProposals,
  onNavigate,
  onSelectDataset,
}: {
  dataset?: Dataset;
  datasets: Dataset[];
  profile: DatasetProfile | null;
  datasetProfiles: Record<string, DatasetProfile>;
  qualityTrends: QualityTrendPoint[];
  proposals: RuleProposal[];
  approvedRules: number;
  loading: boolean;
  busy: boolean;
  canOperate: boolean;
  onStartAnalysis: () => void;
  onRequestProposals: () => void;
  onNavigate: (view: View) => void;
  onSelectDataset?: (datasetId: string) => void;
}) {
  const { language } = useI18n();
  const vi = language === "vi";

  const proposalCount = proposals.filter((proposal) =>
    ["PROPOSED", "EDITED"].includes(proposal.status),
  ).length;
  const qualityRows = datasets.map((item) => {
    const itemProfile =
      datasetProfiles[item.id] ?? (item.id === dataset?.id ? profile : null);
    const score = itemProfile
      ? (itemProfile.completeness_score + itemProfile.validity_score) / 2
      : null;
    return { dataset: item, profile: itemProfile, score };
  });

  const attentionCount = qualityRows.filter((row) => row.score !== null && row.score < 85).length;
  const statusRows = [
    {
      label: vi ? "Đã profile" : "Profile ready",
      count: datasets.filter((item) => item.status === "PROFILE_READY").length,
    },
    {
      label: vi ? "Đã nạp dữ liệu" : "Ingested",
      count: datasets.filter((item) => item.status === "INGESTED").length,
    },
    {
      label: vi ? "Đã đăng ký" : "Registered",
      count: datasets.filter((item) => item.status === "REGISTERED").length,
    },
    { label: vi ? "Cần chú ý" : "Needs attention", count: attentionCount },
  ];
  const statusMax = Math.max(1, ...statusRows.map((row) => row.count));
  if (!dataset)
    return (
      <>
        <div className="page-heading">
          <div>
            <span className="eyebrow">{vi ? "TRUNG TÂM ĐIỀU HÀNH CHẤT LƯỢNG" : "QUALITY COMMAND CENTER"}</span>
            <h1>{vi ? "Chưa có bộ dữ liệu nào" : "No registered dataset"}</h1>
            <p>{vi ? "Hệ thống backend chưa đăng ký bộ dữ liệu nào." : "The backend has not registered a Gate 2 dataset yet."}</p>
          </div>
        </div>
        <section className="empty-state">
          <div className="empty-illustration">▦</div>
          <h2>{vi ? "Danh mục dữ liệu đang trống" : "Dataset catalog is empty"}</h2>
          <p>
            {vi
              ? "Tải lên hoặc đăng ký bộ dữ liệu để xem bảng điều khiển chất lượng."
              : "Upload or register a dataset to populate the multi-dataset quality dashboard."}
          </p>
        </section>
      </>
    );

  const selectedProfile = datasetProfiles[dataset.id] ?? profile;
  const selectedColumns = selectedProfile?.columns ?? [];
  const nullColumnCount = selectedColumns.filter(
    (column) => column.null_rate > 0,
  ).length;
  return (
    <>
      <div className="page-heading overview-heading">
        <div>
          <span className="eyebrow">{vi ? "HỒ SƠ CHẤT LƯỢNG" : "QUALITY PROFILE"}</span>
          <h1>{dataset.name}</h1>
          <p>
            {dataset.source_label} ·{" "}
            {selectedProfile
              ? vi
                ? `Profile lúc ${new Date(selectedProfile.generated_at).toLocaleString("vi-VN")}`
                : `Profiled ${new Date(selectedProfile.generated_at).toLocaleString()}`
              : vi
                ? "Chưa có hồ sơ tổng hợp — bấm Profile dữ liệu ở bước 1"
                : "No aggregate profile yet — run Profile dataset in step 1"}
          </p>
        </div>
        <div className="heading-actions">
          <StatusPill
            label={
              vi
                ? dataset.status === "REGISTERED"
                  ? "ĐÃ ĐĂNG KÝ"
                  : dataset.status === "PROFILE_READY"
                    ? "ĐÃ PROFILE"
                    : dataset.status.replaceAll("_", " ")
                : dataset.status.replaceAll("_", " ")
            }
            tone={dataset.status === "PROFILE_READY" ? "success" : "info"}
          />
          <button
            className="button ghost"
            onClick={() => onNavigate("datasets")}
          >
            {vi ? "Danh mục bộ dữ liệu →" : "Dataset catalog →"}
          </button>
        </div>
      </div>
      <section className="stat-grid overview-kpis">
        <StatCard
          label={vi ? "Số dòng" : "Rows"}
          value={(selectedProfile?.row_count ?? dataset.row_count).toLocaleString()}
          detail={selectedProfile ? (vi ? "Đã đếm trong quá trình profile" : "Counted during profiling") : (vi ? "Khai báo khi đăng ký" : "Declared at registration")}
          tone="blue"
        />
        <StatCard
          label={vi ? "Số cột" : "Columns"}
          value={selectedColumns.length ? `${selectedColumns.length}` : "—"}
          detail={selectedColumns.length ? (vi ? "Cột đã được profile" : "Fields profiled") : (vi ? "Chờ dữ liệu profile" : "Awaiting profile data")}
          tone="violet"
        />
        <StatCard
          label={vi ? "Độ đầy đủ" : "Completeness"}
          value={
            selectedProfile ? `${selectedProfile.completeness_score.toFixed(1)}%` : "—"
          }
          detail={
            selectedProfile
              ? (vi ? `${nullColumnCount} cột có chứa giá trị null` : `${nullColumnCount} column${nullColumnCount === 1 ? "" : "s"} contain nulls`)
              : (vi ? "Chờ dữ liệu profile" : "Awaiting profile data")
          }
          tone={
            selectedProfile && selectedProfile.completeness_score < 95 ? "amber" : "green"
          }
        />
        <StatCard
          label={vi ? "Độ hợp lệ" : "Validity"}
          value={selectedProfile ? `${selectedProfile.validity_score.toFixed(1)}%` : "—"}
          detail={selectedProfile ? (vi ? "Giá trị khớp với kiểu dữ liệu" : "Values matching their declared type") : (vi ? "Chờ dữ liệu profile" : "Awaiting profile data")}
          tone={selectedProfile && selectedProfile.validity_score < 95 ? "amber" : "green"}
        />
        <StatCard
          label={vi ? "Tỷ lệ trùng lặp" : "Duplicate rate"}
          value={selectedProfile ? `${selectedProfile.duplicate_rate.toFixed(2)}%` : "—"}
          detail={
            selectedProfile && selectedProfile.duplicate_rate > 0
              ? (vi ? "Phát hiện dòng bị trùng lặp" : "Repeated rows detected")
              : (vi ? "Không có dòng trùng lặp" : "No duplicate rows detected")
          }
          tone={selectedProfile && selectedProfile.duplicate_rate > 0 ? "amber" : "green"}
        />
        <StatCard
          label={vi ? "Quy tắc đề xuất" : "Rules proposed"}
          value={`${proposalCount}`}
          detail={
            approvedRules
              ? (vi ? `${approvedRules} quy tắc đã duyệt đang hoạt động` : `${approvedRules} approved rule${approvedRules === 1 ? "" : "s"} active`)
              : (vi ? "Chờ xem xét ở bước 3" : "Awaiting review in step 3")
          }
          tone={proposalCount ? "amber" : "blue"}
        />
      </section>
      <section className="overview-grid">
        <article className="panel overview-dataset-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{vi ? "BẢN ĐỒ CHẤT LƯỢNG DANH MỤC" : "CATALOG QUALITY MAP"}</span>
              <h3>{vi ? "Chất lượng theo bộ dữ liệu" : "Quality by dataset"}</h3>
            </div>
            <span className="panel-caption">{vi ? `${datasets.length} bộ dữ liệu · Bấm để chọn` : `${datasets.length} registered · Click to select`}</span>
          </div>
          <div className="overview-dataset-list">
            {qualityRows.map((row) => (
              <div
                className={`overview-dataset-row ${row.dataset.id === dataset.id ? "active" : ""}`}
                key={row.dataset.id}
                onClick={() => onSelectDataset?.(row.dataset.id)}
                style={{ cursor: "pointer" }}
              >
                <div className="overview-dataset-id">
                  <span className="dataset-mini-icon">⌁</span>
                  <div>
                    <strong>{row.dataset.name}</strong>
                    <small style={{ display: "block", color: "var(--muted)", fontSize: "11px" }}>
                      {row.dataset.source_label} ·{" "}
                      {row.dataset.row_count.toLocaleString()} {vi ? "dòng" : "rows"}
                    </small>
                  </div>
                </div>
                <StatusPill
                  label={
                    vi
                      ? row.dataset.status === "REGISTERED"
                        ? "ĐÃ ĐĂNG KÝ"
                        : row.dataset.status === "PROFILE_READY"
                          ? "ĐÃ PROFILE"
                          : row.dataset.status.replaceAll("_", " ")
                      : row.dataset.status.replaceAll("_", " ")
                  }
                  tone={
                    row.dataset.status === "PROFILE_READY" ? "success" : "info"
                  }
                />
                <div className="overview-dataset-score">
                  {row.score === null ? (
                    <span className="muted">{vi ? "Chờ profile" : "Profile pending"}</span>
                  ) : (
                    <>
                      <div className="overview-score-track">
                        <span style={{ width: `${row.score}%` }} />
                      </div>
                      <strong style={{ fontSize: "13px", marginLeft: "6px" }}>{row.score.toFixed(1)}%</strong>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </article>
        <article className="panel overview-status-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{vi ? "TRẠNG THÁI DANH MỤC" : "CATALOG STATUS"}</span>
              <h3>{vi ? "Phân bố trạng thái sẵn sàng" : "Readiness distribution"}</h3>
            </div>
            <span className="panel-caption">
              {vi ? `${approvedRules} quy tắc đã duyệt đang hoạt động` : `${approvedRules} approved rules active`}
            </span>
          </div>
          <div className="overview-status-list">
            {statusRows.map((row) => (
              <div className="overview-status-row" key={row.label}>
                <div>
                  <span>{row.label}</span>
                  <strong>{row.count}</strong>
                </div>
                <div className="overview-status-track">
                  <span
                    style={{ width: `${(row.count / statusMax) * 100}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
          <div className="overview-status-footer">
            <span>{vi ? "Hàng đợi xem xét" : "Review queue"}</span>
            <strong>{vi ? `${proposalCount} chờ xử lý` : `${proposalCount} pending`}</strong>
          </div>
        </article>
      </section>
      <section className="overview-chart-grid">
        <article className="panel overview-trend-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{vi ? "XU HƯỚNG BỘ DỮ LIỆU ĐANG CHỌN" : "ACTIVE DATASET TREND"}</span>
              <h3>{vi ? "Điểm chất lượng theo thời gian" : "Quality score over time"}</h3>
            </div>
            <button
              className="text-button"
              onClick={() => onNavigate("visualization")}
            >
              {vi ? "Mở chế độ xem đầy đủ →" : "Open full view →"}
            </button>
          </div>
          <TrendChart points={qualityTrends} />
        </article>
        <article className="panel overview-compare-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{vi ? "SO SÁNH CHẤT LƯỢNG" : "QUALITY COMPARISON"}</span>
              <h3>{vi ? "Độ đầy đủ vs Độ hợp lệ" : "Completeness vs validity"}</h3>
            </div>
            <span className="panel-caption">{vi ? "Chỉ bộ dữ liệu đã profile" : "Profiled datasets only"}</span>
          </div>
          <OverviewQualityBars rows={qualityRows} />
        </article>
      </section>
      {/* Tạm thời ẩn theo yêu cầu người dùng (giữ nguyên mã nguồn) */}
      {false && (
        <section className="overview-action-panel next-panel">
          <div>
            <span className="eyebrow">{vi ? "HÀNH ĐỘNG TIẾP THEO" : "NEXT ACTION"}</span>
            <h3>
              {profile
                ? (vi ? "Tiếp tục quy trình đang làm việc" : "Continue the active pipeline")
                : (vi ? "Tạo hồ sơ profile đầu tiên" : "Build the first profile")}
            </h3>
            <p>
              {profile
                ? (vi ? "Bộ dữ liệu đã được profile. Chuyển sang bước Đề xuất quy tắc để xem tiếp các bước agent." : "The active dataset is profiled. Move into Rule proposer to review the next agent step.")
                : (vi ? "Chạy nạp dữ liệu và profile để bộ dữ liệu sẵn sàng cho việc so sánh." : "Run ingestion and profiling to make this dataset available for cross-dataset comparison.")}
            </p>
          </div>
          <div className="overview-action-buttons">
            {canOperate &&
              (!profile ? (
                <button
                  className="button secondary"
                  onClick={onStartAnalysis}
                  disabled={loading || busy}
                >
                  {vi ? "Bắt đầu profile →" : "Start profiling →"}
                </button>
              ) : proposalCount ? (
                <button
                  className="button secondary"
                  onClick={() => onNavigate("rules")}
                >
                  {vi ? "Mở hàng đợi duyệt →" : "Open review queue →"}
                </button>
              ) : (
                <button
                  className="button secondary"
                  onClick={onRequestProposals}
                  disabled={busy}
                >
                  {vi ? "Sinh đề xuất quy tắc →" : "Generate proposals →"}
                </button>
              ))}
            <button className="button ghost" onClick={() => onNavigate("audit")}>
              {vi ? "Xem lịch sử nhật ký" : "View audit trail"}
            </button>
          </div>
        </section>
      )}
    </>
  );
}

function OverviewQualityBars({
  rows,
}: {
  rows: Array<{
    dataset: Dataset;
    profile: DatasetProfile | null;
    score: number | null;
  }>;
}) {
  const { language } = useI18n();
  const vi = language === "vi";

  return (
    <div className="overview-quality-bars">
      {rows.map((row) => (
        <div className="overview-quality-bar" key={row.dataset.id}>
          <div className="overview-quality-label">
            <strong>{row.dataset.name}</strong>
            <span>
              {row.score === null
                ? (vi ? "Chờ profile" : "Profile pending")
                : (vi ? `${row.score.toFixed(1)}% tổng thể` : `${row.score.toFixed(1)}% overall`)}
            </span>
          </div>
          <div className="overview-quality-lines">
            <div>
              <span>{vi ? "Độ đầy đủ" : "Completeness"}</span>
              <div className="overview-line-track">
                <i
                  style={{ width: `${row.profile?.completeness_score ?? 0}%` }}
                />
              </div>
              <strong>
                {row.profile
                  ? `${row.profile.completeness_score.toFixed(1)}%`
                  : "—"}
              </strong>
            </div>
            <div>
              <span>{vi ? "Độ hợp lệ" : "Validity"}</span>
              <div className="overview-line-track validity">
                <i style={{ width: `${row.profile?.validity_score ?? 0}%` }} />
              </div>
              <strong>
                {row.profile
                  ? `${row.profile.validity_score.toFixed(1)}%`
                  : "—"}
              </strong>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function StatCard({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: string;
  detail: string;
  tone: string;
}) {
  // Chấm màu lấy từ token theo `tone` trong styles.css thay vì mã hex tại chỗ,
  // để nó đổi cùng chủ đề như phần còn lại của thẻ.
  return (
    <div className={`stat-card ${tone}`}>
      <div className="stat-card-top">
        <span className="stat-label">{label}</span>
        <span className={`stat-dot ${tone}`} />
      </div>
      <strong className="stat-value">{value}</strong>
      <span className="stat-detail">{detail}</span>
    </div>
  );
}

function RulesPage({
  proposals,
  configurations,
  profileReady,
  busy,
  canOperate,
  onRequestProposals,
  onApprove,
  onReject,
  onEdit,
  onDelete,
  onSaveConfiguration,
  onCreateManual,
  onRun,
  pipelineMode = false,
}: {
  proposals: RuleProposal[];
  configurations: RuleConfiguration[];
  profileReady: boolean;
  busy: boolean;
  canOperate: boolean;
  onRequestProposals: () => void;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  onEdit: (proposal: RuleProposal) => void;
  onDelete: (id: string) => void;
  onSaveConfiguration: (id: string, input: RuleConfigurationInput) => void;
  onCreateManual: () => void;
  onRun: () => void;
  pipelineMode?: boolean;
}) {
  const [expandedConfigurationId, setExpandedConfigurationId] = useState<
    string | null
  >(null);
  const pending = proposals.filter((proposal) =>
    ["PROPOSED", "EDITED"].includes(proposal.status),
  );
  const { t, language } = useI18n();
  const approved = proposals.filter(
    (proposal) => proposal && proposal.status === "APPROVED",
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            {pipelineMode ? "PIPELINE STAGE 4" : "HUMAN-IN-THE-LOOP"}
          </span>
          <h1>
            {pipelineMode
              ? "Review rules before code generation"
              : "Rule proposals"}
          </h1>
          <p>
            {pipelineMode
              ? "Accept, edit, reject or add a manual rule. Agent stays locked until this set is approved."
              : "Review agent suggestions or author a typed rule manually."}
          </p>
        </div>
        <div className="heading-actions">
          {/* Buttons removed from heading as per user request to avoid duplication with empty state */}
        </div>
      </div>
      {busy ? (
        <section className="panel" style={{ padding: "48px 24px", textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", background: "var(--surface)", border: "1px dashed var(--border)", borderRadius: "16px", marginTop: "16px" }}>
          <div className="workflow-pending-indicator" style={{ width: "32px", height: "32px", marginBottom: "16px" }} />
          <h2 style={{ fontSize: "18px", fontWeight: "700", color: "var(--ink)", margin: "0 0 8px 0" }}>
            {t("rules.generatingRules") || "Đang sinh quy tắc…"}
          </h2>
          <p style={{ color: "var(--muted)", fontSize: "14px", margin: 0, maxWidth: "480px" }}>
            {t("rules.runningProposerDesc") || "AI Agent đang phân tích dữ liệu và tự động khởi tạo các quy tắc kiểm soát chất lượng."}
          </p>
        </section>
      ) : !profileReady ? (
        <section className="empty-state">
          <div className="empty-illustration">✦</div>
          <h2>{t("rules.profileFirstTitle")}</h2>
          <p>{t("rules.profileFirstDesc")}</p>
          {canOperate && (
            <button className="button secondary" onClick={onCreateManual}>
              + Add manual rule
            </button>
          )}
          {!pipelineMode && (
            <button
              className="button primary"
              onClick={onRun}
              disabled={!approved.length || busy || !canOperate}
            >
              Run approved rules <span>→</span>
            </button>
          )}
        </section>
      ) : !proposals.length ? (
        <section className="empty-state">
          <div className="empty-illustration">✦</div>
          <h2>No proposals yet</h2>
          <p>Start with an Agent proposal or create a typed rule manually.</p>
          {canOperate && (
            <div className="dialog-actions">
              <button className="button secondary" onClick={onCreateManual}>
                Add manual rule
              </button>
              <button
                className="button primary"
                onClick={onRequestProposals}
                disabled={busy}
              >
                Generate proposals →
              </button>
            </div>
          )}
        </section>
      ) : (
        <>
          <ReviewSummaryPanel proposals={proposals} />
          <div className="proposal-list">
            {proposals.map((proposal) => (
              <ProposalCard
                key={proposal.id}
                proposal={proposal}
                canOperate={canOperate}
                onApprove={() => onApprove(proposal.id)}
                onReject={() => onReject(proposal.id)}
                onEdit={() => onEdit(proposal)}
                onDelete={() => onDelete(proposal.id)}
                configuration={configurations.find(
                  (item) => item.rule_id === proposal.id,
                )}
                onSaveConfiguration={(input) =>
                  onSaveConfiguration(proposal.id, input)
                }
                configurationExpanded={expandedConfigurationId === proposal.id}
                onToggleConfiguration={() =>
                  setExpandedConfigurationId((current) =>
                    current === proposal.id ? null : proposal.id,
                  )
                }
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}

function ProposalCard({
  proposal,
  canOperate,
  onApprove,
  onReject,
  onEdit,
  onDelete,
  configuration,
  onSaveConfiguration,
  configurationExpanded,
  onToggleConfiguration,
}: {
  proposal: RuleProposal;
  canOperate: boolean;
  onApprove: () => void;
  onReject: () => void;
  onEdit: () => void;
  onDelete: () => void;
  configuration?: RuleConfiguration;
  onSaveConfiguration: (input: RuleConfigurationInput) => void;
  configurationExpanded: boolean;
  onToggleConfiguration: () => void;
}) {
  const pending = ["PROPOSED", "EDITED"].includes(proposal.status);
  const editable = pending || proposal.status === "APPROVED";
  const canApprove = proposal.status !== "APPROVED";
  const canReject = proposal.status !== "REJECTED";
  const tone =
    proposal.status === "REJECTED"
      ? "danger"
      : proposal.status === "APPROVED"
        ? "success"
        : "warning";
  return (
    <article className={`proposal-card ${proposal.status.toLowerCase()}`}>
      <div className="proposal-top">
        <div className={`rule-type ${proposal.rule.type}`}>
          <span>✦</span>
          {proposal.rule.type.replaceAll("_", " ")}
        </div>
        <span className="proposal-source">
          {proposal.source === "MANUAL" ? "Manual rule" : "Agent proposal"}
        </span>
        <StatusPill label={proposal.status} tone={tone} />
        <span className={`severity ${proposal.severity.toLowerCase()}`}>
          {proposal.severity} severity
        </span>
      </div>
      <div className="proposal-main">
        <div className="proposal-content">
          <h3>{proposal.title}</h3>
          <p>{proposal.description}</p>
          <div className="rule-code">
            <span>TYPE</span>
            <code>{formatRule(proposal.rule)}</code>
          </div>
        </div>
        <div className="confidence">
          <span>CONFIDENCE</span>
          <strong>{Math.round(proposal.confidence * 100)}%</strong>
          <div className="confidence-track">
            <span style={{ width: `${proposal.confidence * 100}%` }} />
          </div>
        </div>
      </div>
      <div className="evidence-row">
        <span className="evidence-label">EVIDENCE</span>
        <span>{proposal.evidence_summary}</span>
        {proposal.evidence_refs.map((ref) => (
          <code key={ref}>{ref}</code>
        ))}
      </div>
      <ProposalRationale proposal={proposal} />
      {(editable || proposal.status === "REJECTED") && canOperate && (
        <div className="proposal-actions">
          {canReject && (
            <button className="button ghost proposal-action reject" onClick={onReject}>
              {proposal.status === "APPROVED"
                ? "Reject approved rule"
                : "Reject"}
            </button>
          )}
          <button className="button secondary proposal-action edit" onClick={onEdit}>
            {pending
              ? "Edit"
              : proposal.status === "APPROVED"
                ? "Edit approved rule"
                : "Edit rejected rule"}
          </button>
          {/* Kept on screen even once it no longer applies. Hiding it left an
              approved rule showing only "reject" and "edit", which reads as a
              missing action rather than as a decision already made. */}
          <button
            className="button primary proposal-action approve"
            onClick={onApprove}
            disabled={!canApprove}
            title={canApprove ? undefined : "Rule đã được duyệt"}
          >
            {!canApprove
              ? "✓ Đã duyệt"
              : proposal.status === "REJECTED"
                ? "Re-approve rule"
                : "Approve rule"}
            {canApprove && <span> →</span>}
          </button>
          {proposal.status !== "APPROVED" && (
            <button className="button ghost proposal-action delete" onClick={onDelete}>
              Delete
            </button>
          )}
        </div>
      )}
      {proposal.status === "APPROVED" && canOperate && (
        <RuleConfigurationControl
          configuration={configuration}
          expanded={configurationExpanded}
          onToggle={onToggleConfiguration}
          onSave={onSaveConfiguration}
        />
      )}
    </article>
  );
}

const BASIS_LABEL: Record<ProposalBasis, string> = {
  SCHEMA_CONSTRAINT: "Ràng buộc schema",
  DATA_PROFILE: "Hồ sơ dữ liệu",
  DATA_DICTIONARY: "Từ điển dữ liệu",
  HISTORICAL_RULE: "Luật đã dùng trước đây",
  POLICY: "Chính sách quản trị",
  MIXED: "Nhiều nguồn",
};

/**
 * Phần "vì sao" của một đề xuất luật.
 *
 * Backend vẫn luôn trả về lý do nghiệp vụ, nguồn gốc từng tham số, các giả định
 * và phân rã độ tin cậy — nhưng thẻ đề xuất chỉ hiển thị tiêu đề, mô tả và một
 * con số phần trăm trần trụi. Steward vì thế phải duyệt bằng cảm tính, đúng thứ
 * mà chốt chặn HITL sinh ra để ngăn.
 *
 * Mặc định thu gọn: hàng đợi duyệt thường dài, và người đọc chỉ cần mở ra ở
 * những luật họ phân vân.
 */
function ProposalRationale({ proposal }: { proposal: RuleProposal }) {
  const [open, setOpen] = useState(false);

  const provenance = proposal.parameter_provenance ?? [];
  const assumptions = proposal.assumptions ?? [];
  const breakdown = proposal.confidence_breakdown;
  const hasDetail =
    Boolean(proposal.business_rationale) ||
    provenance.length > 0 ||
    assumptions.length > 0 ||
    Boolean(breakdown);

  if (!hasDetail) {
    // Không bịa ra chỗ trống trông như đã có nội dung: nói thẳng là agent không
    // kèm lý do, vì đó cũng là một tín hiệu để người duyệt cân nhắc.
    return (
      <p className="rationale-absent">
        Đề xuất này không kèm lý do chi tiết
        {proposal.model_name ? ` (${proposal.model_name})` : ""}.
      </p>
    );
  }

  return (
    <div className="rationale">
      <div className="rationale-head">
        <button
          type="button"
          className="rationale-toggle"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          <span className="rationale-caret">{open ? "▾" : "▸"}</span>
          Vì sao có luật này
        </button>
        {proposal.proposal_basis && (
          <span className={`basis-badge ${proposal.proposal_basis.toLowerCase()}`}>
            {BASIS_LABEL[proposal.proposal_basis]}
          </span>
        )}
        {proposal.model_name && (
          <span className="rationale-model">{proposal.model_name}</span>
        )}
      </div>

      {open && (
        <div className="rationale-body">
          {proposal.business_rationale && (
            <section className="rationale-block">
              <h4>Lý do nghiệp vụ</h4>
              <p>{proposal.business_rationale}</p>
            </section>
          )}

          {breakdown && (
            <section className="rationale-block">
              <h4>Độ tin cậy đến từ đâu</h4>
              <div className="confidence-bars">
                {(
                  [
                    ["Sức mạnh bằng chứng", breakdown.evidence_strength],
                    ["Ủng hộ từ nghiệp vụ", breakdown.business_support],
                    ["Tính đại diện của mẫu", breakdown.sample_representativeness],
                  ] as const
                ).map(([label, value]) => (
                  <div className="confidence-bar" key={label}>
                    <span className="cb-label">{label}</span>
                    <span className="cb-track">
                      <span style={{ width: `${Math.round(value * 100)}%` }} />
                    </span>
                    <span className="cb-value">{Math.round(value * 100)}%</span>
                  </div>
                ))}
              </div>
              {breakdown.explanation && (
                <p className="rationale-note">{breakdown.explanation}</p>
              )}
            </section>
          )}

          {provenance.length > 0 && (
            <section className="rationale-block">
              <h4>Tham số lấy từ đâu</h4>
              <div className="rationale-table-scroll">
                <table className="rationale-table">
                  <thead>
                    <tr>
                      <th>Tham số</th>
                      <th>Nguồn</th>
                      <th>Tham chiếu</th>
                      <th>Cách suy ra</th>
                    </tr>
                  </thead>
                  <tbody>
                    {provenance.map((item) => (
                      <tr key={`${item.parameter_name}-${item.source_ref}`}>
                        <td>
                          <code>{item.parameter_name}</code>
                        </td>
                        <td>{BASIS_LABEL[item.source_type]}</td>
                        <td>
                          <code>{item.source_ref}</code>
                        </td>
                        <td>{item.derivation_method}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {assumptions.length > 0 && (
            <section className="rationale-block">
              <h4>Agent đã giả định</h4>
              <ul className="rationale-list">
                {assumptions.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

function RuleConfigurationControl({
  configuration,
  expanded,
  onToggle,
  onSave,
}: {
  configuration?: RuleConfiguration;
  expanded: boolean;
  onToggle: () => void;
  onSave: (input: RuleConfigurationInput) => void;
}) {
  const [executionStatus, setExecutionStatus] = useState<
    RuleConfiguration["execution_status"]
  >(configuration?.execution_status ?? "ACTIVE");
  const [frequency, setFrequency] = useState<
    RuleConfiguration["schedule_frequency"]
  >(configuration?.schedule_frequency ?? "MANUAL");
  const [timezone, setTimezone] = useState(configuration?.timezone ?? "UTC");
  useEffect(() => {
    setExecutionStatus(configuration?.execution_status ?? "ACTIVE");
    setFrequency(configuration?.schedule_frequency ?? "MANUAL");
    setTimezone(configuration?.timezone ?? "UTC");
  }, [configuration]);
  const frequencyLabel =
    frequency === "MANUAL"
      ? "Manual only"
      : frequency === "HOURLY"
        ? "Hourly"
        : "Daily";
  const panelId = `rule-settings-${configuration?.rule_id ?? "default"}`;
  return (
    <section className={`rule-settings-shell ${expanded ? "expanded" : ""}`}>
      <button
        type="button"
        className="rule-settings-summary"
        onClick={onToggle}
        aria-expanded={expanded}
        aria-controls={panelId}
      >
        <span
          className={`configuration-state ${executionStatus.toLowerCase()}`}
        >
          <i />
          {executionStatus === "ACTIVE" ? "Active" : "Paused"}
        </span>
        <span className="configuration-summary">
          <strong>Execution settings</strong>
          <small>
            {frequencyLabel} · {timezone}
          </small>
        </span>
        <span className="configuration-action">
          {expanded ? "Hide options" : "Configure"}
          <i aria-hidden="true">⌄</i>
        </span>
      </button>
      {expanded && (
        <div className="rule-settings" id={panelId}>
          <div className="rule-settings-fields">
            <label>
              Status
              <select
                value={executionStatus}
                onChange={(event) =>
                  setExecutionStatus(
                    event.target.value as RuleConfiguration["execution_status"],
                  )
                }
              >
                <option value="ACTIVE">Active</option>
                <option value="PAUSED">Paused</option>
              </select>
            </label>
            <label>
              Schedule
              <select
                value={frequency}
                onChange={(event) =>
                  setFrequency(
                    event.target
                      .value as RuleConfiguration["schedule_frequency"],
                  )
                }
              >
                <option value="MANUAL">Manual only</option>
                <option value="HOURLY">Hourly</option>
                <option value="DAILY">Daily</option>
              </select>
            </label>
            <label>
              Timezone
              <input
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
                aria-label="Timezone"
              />
            </label>
            <button
              className="button ghost"
              onClick={() =>
                onSave({
                  execution_status: executionStatus,
                  schedule_frequency: frequency,
                  timezone,
                })
              }
            >
              Save settings
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

/**
 * Bộ luật đang canh dữ liệu thật.
 *
 * Trước đây `/dq/active-rules` chưa từng được gọi, nên người dùng duyệt luật ở
 * bước 3 mà không có chỗ nào xác nhận luật nào đã thực sự được xuất bản và đang
 * chạy. Đề xuất và luật đang hoạt động là hai thứ khác nhau.
 */
function ActiveRulesPanel({ datasetId }: { datasetId: string }) {
  const [rules, setRules] = useState<ActiveRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getActiveRules(datasetId)
      .then((next) => {
        if (!cancelled) setRules(next);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(
          cause instanceof ApiError
            ? cause.message
            : "Không tải được bộ luật đang hoạt động.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  const active = rules.filter((rule) => rule.status === "ACTIVE");

  return (
    <section className="panel active-rules-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">BỘ LUẬT ĐANG CHẠY</span>
          <h3>Active ruleset</h3>
        </div>
        <span className="panel-caption">
          {active.length} đang hoạt động
          {rules.length !== active.length && ` · ${rules.length - active.length} đã tắt`}
        </span>
      </div>
      {loading ? (
        <p className="investigation-note">Đang tải…</p>
      ) : error ? (
        <p className="investigation-note error">{error}</p>
      ) : rules.length === 0 ? (
        <p className="investigation-note">
          Chưa có luật nào được xuất bản. Duyệt và xuất bản ở bước 3 để luật bắt
          đầu canh dữ liệu.
        </p>
      ) : (
        <div className="active-rules-scroll">
          <table className="active-rules-table">
            <thead>
              <tr>
                <th>Luật</th>
                <th>Cột</th>
                <th>Loại</th>
                <th>Chiều</th>
                <th>Mức</th>
                <th>Trạng thái</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.rule_id}>
                  <td>{rule.rule_description || rule.rule_id}</td>
                  <td>{rule.column ? <code>{rule.column}</code> : "—"}</td>
                  <td>{rule.rule_type.replaceAll("_", " ")}</td>
                  <td className="muted">{rule.dimension}</td>
                  <td>
                    <span className={`severity ${rule.severity.toLowerCase()}`}>
                      {rule.severity}
                    </span>
                  </td>
                  <td>
                    <StatusPill
                      label={rule.status}
                      tone={rule.status === "ACTIVE" ? "success" : "neutral"}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/**
 * Tổng kết tiến độ duyệt luật.
 *
 * Tính tại chỗ từ mảng `proposals` mà giao diện đã tải sẵn, thay vì gọi
 * `/dq/runs/{run_id}/review-summary`: endpoint đó nhận `run_id` của lần sinh
 * luật, còn giao diện chỉ giữ `workflow_run_id` — hai định danh khác nhau. Đếm
 * tại chỗ cho cùng con số mà không phải đoán ánh xạ ID.
 */
function ReviewSummaryPanel({ proposals }: { proposals: RuleProposal[] }) {
  const summary = useMemo(() => {
    const counts = { total: proposals.length, pending: 0, approved: 0, rejected: 0, edited: 0 };
    for (const proposal of proposals) {
      if (proposal.status === "APPROVED") counts.approved += 1;
      else if (proposal.status === "REJECTED") counts.rejected += 1;
      else if (proposal.status === "EDITED") {
        counts.edited += 1;
        counts.pending += 1;
      } else counts.pending += 1;
    }
    return counts;
  }, [proposals]);

  if (summary.total === 0) return null;

  const reviewed = summary.approved + summary.rejected;
  const percent = Math.round((reviewed / summary.total) * 100);

  return (
    <section className="review-summary">
      <div className="rs-head">
        <span className="rs-title">Tiến độ duyệt</span>
        <span className="rs-count">
          {reviewed}/{summary.total} đã quyết định
        </span>
      </div>
      <div className="rs-track">
        <span className="rs-approved" style={{ width: `${(summary.approved / summary.total) * 100}%` }} />
        <span className="rs-rejected" style={{ width: `${(summary.rejected / summary.total) * 100}%` }} />
      </div>
      <div className="rs-legend">
        <span><b className="dot approved" />{summary.approved} duyệt</span>
        <span><b className="dot rejected" />{summary.rejected} từ chối</span>
        <span><b className="dot pending" />{summary.pending} chờ</span>
        {summary.edited > 0 && <span className="rs-edited">{summary.edited} đã sửa tham số</span>}
        <span className="rs-percent">{percent}%</span>
      </div>
    </section>
  );
}

/**
 * Danh sách ID dòng trượt.
 *
 * Trước đây là `ids.join(", ")` — một dải ID dính liền, không đọc được và không
 * sao chép chọn lọc được. Runner cố ý chỉ trả ID chứ không trả giá trị thật
 * (quy tắc an toàn: không để dữ liệu thô rời khỏi ranh giới), nên việc cần làm
 * là trình bày ID cho dễ dùng, không phải hiển thị thêm dữ liệu.
 */
function FailedRowIds({ ids }: { ids: string[] }) {
  const [expanded, setExpanded] = useState(false);
  const LIMIT = 5;

  if (ids.length === 0) return <span className="failed-ids empty">—</span>;

  const shown = expanded ? ids : ids.slice(0, LIMIT);
  const hidden = ids.length - shown.length;

  return (
    <span className="failed-ids">
      {shown.map((id) => (
        <code key={id} title={id}>
          {id}
        </code>
      ))}
      {hidden > 0 && (
        <button
          type="button"
          className="failed-ids-more"
          onClick={() => setExpanded(true)}
        >
          +{hidden} nữa
        </button>
      )}
      {expanded && ids.length > LIMIT && (
        <button
          type="button"
          className="failed-ids-more"
          onClick={() => setExpanded(false)}
        >
          thu gọn
        </button>
      )}
    </span>
  );
}

const FEEDBACK_CHOICES: Array<{ label: AnomalyFeedbackLabel; text: string }> = [
  { label: "TRUE_ANOMALY", text: "Đúng là bất thường" },
  { label: "FALSE_POSITIVE", text: "Báo nhầm" },
  { label: "EXPECTED_CHANGE", text: "Thay đổi đã biết trước" },
  { label: "RULE_MISCONFIGURATION", text: "Luật cấu hình sai" },
];

const HYPOTHESIS_LABEL: Record<string, string> = {
  SYSTEM_BUG: "Lỗi hệ thống",
  SCHEMA_CHANGE: "Thay đổi schema",
  UPSTREAM_DATA_DRIFT: "Dữ liệu nguồn trôi",
  ML_MODEL_DRIFT: "Mô hình trôi",
  OUTLIER: "Giá trị ngoại lai",
  DATA_QUALITY_VIOLATION: "Vi phạm quy tắc chất lượng",
  UNKNOWN: "Chưa xác định",
};

/**
 * Kết quả điều tra nguyên nhân gốc của Graph 3.
 *
 * DeepAgent đã sinh ra giả thuyết, bằng chứng hai chiều và các kiểm tra khuyến
 * nghị từ trước, lưu đầy đủ trong `anomaly_hypotheses` — nhưng ba endpoint
 * `/dq/anomaly-runs/*` chưa từng được giao diện gọi, nên toàn bộ phần suy luận
 * này vô hình với người dùng. Đây là phần khác biệt nhất của sản phẩm so với
 * một công cụ chạy dbt thông thường.
 */
function AnomalyInvestigationPanel({
  runId,
  canOperate,
}: {
  runId: string;
  canOperate: boolean;
}) {
  const [signals, setSignals] = useState<AnomalySignal[]>([]);
  const [hypotheses, setHypotheses] = useState<AnomalyHypothesis[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<AnomalyFeedbackLabel | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSent(null);
    Promise.all([api.getAnomalySignals(runId), api.getAnomalyHypotheses(runId)])
      .then(([nextSignals, nextHypotheses]) => {
        if (cancelled) return;
        setSignals(nextSignals);
        setHypotheses(nextHypotheses);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(
          cause instanceof ApiError
            ? cause.message
            : "Không tải được kết quả điều tra.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const signalById = useMemo(
    () => new Map(signals.map((signal) => [signal.signal_id, signal])),
    [signals],
  );

  const describeSignal = (signalId: string) => {
    const signal = signalById.get(signalId);
    if (!signal) return signalId;
    const score = signal.score.toFixed(2);
    return `${signal.explanation_code} · ${signal.target_id} (điểm ${score})`;
  };

  const sendFeedback = async (label: AnomalyFeedbackLabel) => {
    setSending(true);
    try {
      await api.submitAnomalyFeedback(runId, { feedback_label: label });
      setSent(label);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : "Không gửi được phản hồi.",
      );
    } finally {
      setSending(false);
    }
  };

  return (
    <section className="panel investigation-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">ĐIỀU TRA NGUYÊN NHÂN GỐC</span>
          <h3>Giả thuyết từ agent</h3>
        </div>
        <span className="panel-caption">
          {signals.length} tín hiệu · {hypotheses.length} giả thuyết
        </span>
      </div>

      {loading ? (
        <p className="investigation-note">Đang tải kết quả điều tra…</p>
      ) : error ? (
        <p className="investigation-note error">{error}</p>
      ) : hypotheses.length === 0 ? (
        <p className="investigation-note">
          Chưa có giả thuyết nào cho lần chạy này. Agent điều tra chỉ chạy khi bộ
          phát hiện thống kê tìm thấy tín hiệu bất thường.
        </p>
      ) : (
        <div className="hypothesis-list">
          {hypotheses.map((hypothesis) => (
            <article className="hypothesis" key={hypothesis.id}>
              <div className="hypothesis-top">
                <span className="hypothesis-type">
                  {HYPOTHESIS_LABEL[hypothesis.hypothesis_type] ??
                    hypothesis.hypothesis_type.replaceAll("_", " ")}
                </span>
                {hypothesis.fallback_used && (
                  <span className="hypothesis-fallback" title="Agent phải dùng đường lui, độ tin cậy thấp hơn bình thường">
                    đường lui
                  </span>
                )}
                <span className="hypothesis-confidence">
                  <span className="hc-track">
                    <span style={{ width: `${Math.round(hypothesis.confidence * 100)}%` }} />
                  </span>
                  <strong>{Math.round(hypothesis.confidence * 100)}%</strong>
                </span>
              </div>

              <p className="hypothesis-summary">{hypothesis.summary}</p>

              <div className="hypothesis-evidence">
                <div className="he-block">
                  <h5>Bằng chứng ủng hộ</h5>
                  {hypothesis.supporting_signal_ids.length === 0 ? (
                    <p className="he-empty">Không có</p>
                  ) : (
                    <ul>
                      {hypothesis.supporting_signal_ids.map((id) => (
                        <li key={id}>{describeSignal(id)}</li>
                      ))}
                    </ul>
                  )}
                </div>
                <div className="he-block against">
                  <h5>Bằng chứng phản bác</h5>
                  {hypothesis.contradicting_signal_ids.length === 0 ? (
                    <p className="he-empty">Không có</p>
                  ) : (
                    <ul>
                      {hypothesis.contradicting_signal_ids.map((id) => (
                        <li key={id}>{describeSignal(id)}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>

              {hypothesis.recommended_checks.length > 0 && (
                <div className="hypothesis-actions-block">
                  <h5>Nên kiểm tra tiếp</h5>
                  <ol>
                    {hypothesis.recommended_checks.map((check) => (
                      <li key={check}>{check}</li>
                    ))}
                  </ol>
                </div>
              )}

              {(hypothesis.limitations || hypothesis.missing_evidence) && (
                <p className="hypothesis-limits">
                  {hypothesis.limitations}
                  {hypothesis.limitations && hypothesis.missing_evidence ? " " : ""}
                  {hypothesis.missing_evidence
                    ? `Còn thiếu: ${hypothesis.missing_evidence}`
                    : ""}
                </p>
              )}
            </article>
          ))}

          {canOperate && (
            <div className="feedback-bar">
              <span className="feedback-label">Kết luận của bạn về lần điều tra này</span>
              {sent ? (
                <span className="feedback-sent">
                  Đã ghi nhận:{" "}
                  {FEEDBACK_CHOICES.find((choice) => choice.label === sent)?.text ?? sent}
                </span>
              ) : (
                <div className="feedback-buttons">
                  {FEEDBACK_CHOICES.map((choice) => (
                    <button
                      key={choice.label}
                      type="button"
                      className="button secondary"
                      disabled={sending}
                      onClick={() => void sendFeedback(choice.label)}
                    >
                      {choice.text}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function RunsPage({
  activeRun,
  results,
  anomalies,
  approvedCount,
  busy,
  canOperate,
  onRun,
  graphPanel,
  datasetId,
}: {
  activeRun: DqRun | null;
  results: DqResult[];
  anomalies: DqAnomaly[];
  approvedCount: number;
  busy: boolean;
  canOperate: boolean;
  onRun: () => void;
  /** Graph 2 node detail, wired by App. */
  graphPanel?: ReactNode;
  /** Resets the reveal when the user switches dataset. */
  datasetId?: string;
}) {
  const { language } = useI18n();
  const vi = language === "vi";
  // The workspace refresh loads the most recent run so the data is there when
  // it is wanted, but arriving on this step used to show a finished run the
  // user had not asked for -- it read as if pressing the button had already
  // happened. Results appear when this run is started, or when the previous one
  // is opened on purpose.
  const [revealed, setRevealed] = useState(false);
  const hasPriorRun = Boolean(activeRun);
  useEffect(() => {
    // A run in flight while this page is open is this user's doing, so show it.
    if (busy) setRevealed(true);
  }, [busy]);
  useEffect(() => {
    // Keyed on the dataset, not the run id. Resetting whenever the run id
    // changed hid the results of the run the user had just started -- the id
    // changes the moment the new run is created -- and closed the previous run
    // again whenever a workspace refresh returned a different latest run.
    setRevealed(false);
  }, [datasetId]);

  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">READ-ONLY EXECUTION</span>
          <h1>DQ runs</h1>
          <p>
            Persisted checks from approved typed rules. Failed results expose
            bounded IDs only.
          </p>
        </div>
        <div className="run-header-actions">
          {hasPriorRun && !revealed && (
            <button className="button secondary" onClick={() => setRevealed(true)}>
              {vi ? "Xem lượt chạy trước" : "View previous run"}
            </button>
          )}
          <button
            className="button primary"
            onClick={() => {
              setRevealed(true);
              onRun();
            }}
            disabled={!approvedCount || busy || !canOperate}
          >
            {vi ? "Chạy luật đã duyệt" : "Run approved rules"} <span>→</span>
          </button>
        </div>
      </div>
      {!activeRun || !revealed ? (
        <section className="empty-state">
          <div className="empty-illustration">↗</div>
          <h2>{hasPriorRun ? (vi ? "Sẵn sàng chạy" : "Ready to run") : vi ? "Chưa có lượt chạy nào" : "No run yet"}</h2>
          <p>
            {hasPriorRun
              ? vi
                ? `Có sẵn kết quả của lượt chạy trước (${activeRun?.rule_ids.length} luật). Chạy lượt mới, hoặc mở lại kết quả cũ.`
                : `A previous run is available (${activeRun?.rule_ids.length} rules). Start a new run, or open the earlier results.`
              : vi
                ? "Duyệt ít nhất một đề xuất ở bước 3, rồi chạy chúng qua bộ thực thi chỉ đọc."
                : "Approve at least one proposal in step 3, then execute it through the read-only runner."}
          </p>
          {canOperate && (
            <div className="run-header-actions">
              {hasPriorRun && (
                <button className="button secondary" onClick={() => setRevealed(true)}>
                  {vi ? "Xem lượt chạy trước" : "View previous run"}
                </button>
              )}
              <button
                className="button primary"
                onClick={() => {
                  setRevealed(true);
                  onRun();
                }}
                disabled={!approvedCount || busy}
              >
                {vi ? "Chạy luật đã duyệt" : "Run approved rules"} →
              </button>
            </div>
          )}
        </section>
      ) : (
        <>
          {graphPanel}
          <div className="run-hero">
            <div>
              <span className="eyebrow">LATEST RUN</span>
              <h2>{activeRun.id}</h2>
              <p>
                Created {formatTime(activeRun.created_at)} ·{" "}
                {activeRun.rule_ids.length} approved rules
              </p>
            </div>
            <StatusPill
              label={activeRun.status}
              tone={activeRun.status === "SUCCEEDED" ? "success" : "info"}
            />
          </div>
          {activeRun.status === "SUCCEEDED" && (
            <div className="stat-grid run-stats">
              <StatCard
                label="Checked rows"
                value={activeRun.total_checked.toLocaleString()}
                detail="Across approved checks"
                tone="blue"
              />
              <StatCard
                label="Failed rows"
                value={activeRun.total_failed.toLocaleString()}
                detail="Bounded result summary"
                tone="amber"
              />
              <StatCard
                label="Rules executed"
                value={`${activeRun.rule_ids.length}`}
                detail="Approved versions only"
                tone="green"
              />
              <StatCard
                label="Raw values"
                value="0"
                detail="Never returned to browser"
                tone="violet"
              />
            </div>
          )}
          {activeRun.status === "SUCCEEDED" && (
            <section
              className={`panel anomaly-panel ${anomalies.length ? "has-anomalies" : "is-clear"}`}
            >
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">ANOMALY DETECTION</span>
                  <h3>
                    {anomalies.length
                      ? "Signals requiring attention"
                      : "No anomalous shifts detected"}
                  </h3>
                </div>
                <StatusPill
                  label={
                    anomalies.length ? `${anomalies.length} detected` : "CLEAR"
                  }
                  tone={anomalies.length ? "warning" : "success"}
                />
              </div>
              <p className="anomaly-method">
                {anomalies.some((item) => item.anomaly_type === "Z_SCORE_SPIKE")
                  ? "Compared with historical violation rates for the same approved rule."
                  : "Cold-start screening uses a bounded violation-rate threshold until five historical runs exist."}
              </p>
              {anomalies.length > 0 && (
                <div className="anomaly-list">
                  {anomalies.map((anomaly) => (
                    <article
                      className="anomaly-card"
                      key={`${anomaly.rule_id}-${anomaly.anomaly_type}`}
                    >
                      <div className="anomaly-card-top">
                        <strong>{anomaly.rule_title}</strong>
                        <span>
                          {anomaly.anomaly_type === "Z_SCORE_SPIKE"
                            ? "Historical spike"
                            : "High failure rate"}
                        </span>
                      </div>
                      <div className="anomaly-metrics">
                        <div>
                          <small>CURRENT</small>
                          <strong>
                            {(anomaly.current_rate * 100).toFixed(2)}%
                          </strong>
                        </div>
                        <div>
                          <small>BASELINE</small>
                          <strong>
                            {anomaly.historical_mean == null
                              ? "Cold start"
                              : `${(anomaly.historical_mean * 100).toFixed(2)}%`}
                          </strong>
                        </div>
                        <div>
                          <small>Z-SCORE</small>
                          <strong>
                            {anomaly.z_score == null
                              ? "—"
                              : anomaly.z_score.toFixed(2)}
                          </strong>
                        </div>
                      </div>
                      <p>{anomaly.reason}</p>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}
          <div className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">RESULTS</span>
                <h3>Rule outcomes</h3>
              </div>
              <span className="panel-caption">{results.length} checks</span>
            </div>
            {results.length ? (
              <div className="results-table">
                <div className="result-header">
                  <span>RULE</span>
                  <span>STATUS</span>
                  <span>CHECKED</span>
                  <span>FAILED</span>
                  <span>FAILED IDS</span>
                </div>
                {results.map((result) => (
                  <div className="result-row" key={result.rule_id}>
                    <strong>{result.rule_title}</strong>
                    <StatusPill
                      label={result.status}
                      tone={
                        result.status === "PASS"
                          ? "success"
                          : result.status === "SKIPPED"
                            ? "warning"
                            : "danger"
                      }
                    />
                    <span>{result.checked_count.toLocaleString()}</span>
                    <strong
                      className={result.failed_count ? "metric-warn" : ""}
                    >
                      {result.failed_count.toLocaleString()}
                      {/* A dataset-level rule is judged on its rate, so show the
                          measured value beside the raw count. */}
                      {typeof result.violation_rate === "number" && (
                        <small className="result-rate">{result.violation_rate.toFixed(2)}%</small>
                      )}
                    </strong>
                    {result.status === "SKIPPED" ? (
                      <span className="result-skipped" title={result.error_message ?? undefined}>
                        {result.error_message ?? (language === "vi" ? "Không thực thi được" : "Not executable")}
                      </span>
                    ) : (
                      <FailedRowIds ids={result.failed_row_ids} />
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="table-empty">
                The runner is preparing bounded results…
              </div>
            )}
          </div>
          {/* The root-cause panel used to live here, but it renders Graph 3's
              output while this page is Graph 2's. It now sits in step 5. */}
        </>
      )}
    </>
  );
}

function TrendChart({ points }: { points: QualityTrendPoint[] }) {
  if (!points.length) {
    return (
      <div className="chart-empty">
        Run approved rules to establish the first quality trend.
      </div>
    );
  }
  const width = 760;
  const height = 260;
  const insetLeft = 48;
  const insetRight = 18;
  const insetTop = 20;
  const insetBottom = 42;
  const scores = points.map((point) => point.quality_score);
  const minimum = Math.max(0, Math.floor(Math.min(...scores) - 4));
  const maximum = Math.min(100, Math.ceil(Math.max(...scores) + 4));
  const range = Math.max(maximum - minimum, 1);
  const coordinates = points.map((point, index) => ({
    x:
      points.length === 1
        ? width / 2
        : insetLeft +
          (index / (points.length - 1)) * (width - insetLeft - insetRight),
    y:
      height -
      insetBottom -
      ((point.quality_score - minimum) / range) *
        (height - insetTop - insetBottom),
    point,
  }));
  const line = coordinates.map(({ x, y }) => `${x},${y}`).join(" ");
  const areaPath =
    coordinates.length > 1
      ? `M ${coordinates[0].x} ${height - insetBottom} L ${coordinates.map(({ x, y }) => `${x} ${y}`).join(" L ")} L ${coordinates.at(-1)!.x} ${height - insetBottom} Z`
      : "";
  const dateLabel = (value: string) =>
    new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
    }).format(new Date(value));
  return (
    <div className="trend-chart-wrap">
      <svg
        className="trend-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Quality score trend across completed DQ runs"
      >
        <defs>
          <linearGradient id="quality-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.2" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 1, 2, 3].map((lineIndex) => {
          const y =
            insetTop + (lineIndex / 3) * (height - insetTop - insetBottom);
          const value = maximum - (lineIndex / 3) * range;
          return (
            <g key={lineIndex}>
              <line
                x1={insetLeft}
                y1={y}
                x2={width - insetRight}
                y2={y}
                className="chart-grid-line"
              />
              <text
                x={insetLeft - 10}
                y={y + 4}
                className="chart-tick"
                textAnchor="end"
              >
                {value.toFixed(0)}%
              </text>
            </g>
          );
        })}
        {areaPath && <path d={areaPath} className="chart-area" />}
        {coordinates.length > 1 && (
          <polyline points={line} className="chart-line" />
        )}
        {coordinates.map(({ x, y, point }) => (
          <g key={point.run_id}>
            <circle cx={x} cy={y} r="12" className="chart-point-halo" />
            <circle cx={x} cy={y} r="5" className="chart-point" />
            {coordinates.length === 1 && (
              <text
                x={x}
                y={y - 22}
                className="chart-value"
                textAnchor="middle"
              >
                {point.quality_score.toFixed(2)}%
              </text>
            )}
            <title>{`${point.quality_score.toFixed(2)}% · ${new Date(point.created_at).toLocaleString()}`}</title>
          </g>
        ))}
        <text x={insetLeft} y={height - 12} className="chart-date">
          {dateLabel(points[0].created_at)}
        </text>
        {points.length > 1 && (
          <text
            x={width - insetRight}
            y={height - 12}
            className="chart-date"
            textAnchor="end"
          >
            {dateLabel(points.at(-1)!.created_at)}
          </text>
        )}
      </svg>
    </div>
  );
}

function AnomalyMonitoringPanel({
  anomalies,
  trends,
}: {
  anomalies: DqAnomaly[];
  trends: QualityTrendPoint[];
}) {
  const { language } = useI18n();
  const vi = language === "vi";

  const historicalReady = trends.length >= 6;
  const detectionMode =
    anomalies[0]?.detection_mode === "HISTORICAL" ||
    (!anomalies.length && historicalReady)
      ? vi ? "Mô hình lịch sử" : "Historical baseline"
      : vi ? "Mô hình khởi đầu lạnh" : "Cold-start screen";
  return (
    <article className="panel anomaly-monitor">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{vi ? "GIÁM SÁT BẤT THƯỜNG TỰ ĐỘNG" : "AUTOMATED ANOMALY DETECTION"}</span>
          <h3>{vi ? "Giám sát tỷ lệ vi phạm" : "Violation-rate monitoring"}</h3>
        </div>
        <StatusPill
          label={
            anomalies.length
              ? (vi ? `${anomalies.length} TÍN HIỆU` : `${anomalies.length} SIGNAL${anomalies.length === 1 ? "" : "S"}`)
              : (vi ? "KHÔNG CÓ TÍN HIỆU" : "NO SIGNALS")
          }
          tone={anomalies.length ? "warning" : "success"}
        />
      </div>
      <div className="anomaly-monitor-layout">
        <div className="anomaly-engine">
          <span className="monitor-label">{vi ? "KHI NÀO CHẠY" : "WHEN IT RUNS"}</span>
          <strong>{vi ? "Sau mỗi lượt chạy kiểm tra chất lượng hoàn thành" : "After every completed DQ run"}</strong>
          <p>
            {vi
              ? "So sánh tỷ lệ vi phạm của từng quy tắc đã duyệt mà không cần đọc dữ liệu thô trên trình duyệt."
              : "It compares each approved rule’s failure rate without reading raw values in the browser."}
          </p>
          <div className="anomaly-engine-state">
            <i />
            <span>{detectionMode}</span>
          </div>
        </div>
        <div className="anomaly-evaluation">
          <div className="anomaly-spec-grid">
            <div>
              <span>{vi ? "Mẫu tối thiểu" : "Minimum sample"}</span>
              <strong>{vi ? "100 dòng" : "100 rows"}</strong>
              <small>{vi ? "bỏ qua lượt kiểm tra nhỏ" : "small checks are ignored"}</small>
            </div>
            <div>
              <span>{vi ? "Khởi đầu lạnh" : "Cold start"}</span>
              <strong>≥ 5.0%</strong>
              <small>{vi ? "cho tới khi có 5 lượt chạy trước" : "until 5 prior runs exist"}</small>
            </div>
            <div>
              <span>{vi ? "Mô hình lịch sử" : "Historical mode"}</span>
              <strong>z ≥ 2.5</strong>
              <small>{vi ? "yêu cầu tỷ lệ > 1.0%" : "also requires rate > 1.0%"}</small>
            </div>
          </div>
          {anomalies.length ? (
            <div className="anomaly-signal-list">
              {anomalies.map((anomaly) => (
                <article
                  className="anomaly-monitor-signal"
                  key={`${anomaly.rule_id}-${anomaly.anomaly_type}`}
                >
                  <div>
                    <strong>{anomaly.rule_title}</strong>
                    <span>
                      {anomaly.anomaly_type === "Z_SCORE_SPIKE"
                        ? (vi ? "Đột biến lịch sử" : "Historical spike")
                        : (vi ? "Tỷ lệ vi phạm cao" : "High violation rate")}
                    </span>
                  </div>
                  <div className="anomaly-monitor-metrics">
                    <span>
                      {vi ? "Hiện tại" : "Current"}{" "}
                      <strong>
                        {(anomaly.current_rate * 100).toFixed(2)}%
                      </strong>
                    </span>
                    <span>
                      {anomaly.historical_mean == null ? (
                        vi ? "Chưa có điểm cơ sở" : "Baseline unavailable"
                      ) : (
                        <>
                          {vi ? "Cơ sở" : "Baseline"}{" "}
                          <strong>
                            {(anomaly.historical_mean * 100).toFixed(2)}%
                          </strong>
                        </>
                      )}
                    </span>
                    <span>
                      {anomaly.z_score == null ? (
                        vi ? `${anomaly.history_size} lượt chạy trước` : `${anomaly.history_size} prior runs`
                      ) : (
                        <>
                          z-score <strong>{anomaly.z_score.toFixed(2)}</strong>
                        </>
                      )}
                    </span>
                  </div>
                  <p>{anomaly.reason}</p>
                </article>
              ))}
            </div>
          ) : (
            <div className="anomaly-clear-state">
              <span>{vi ? "ĐÁNH GIÁ MỚI NHẤT" : "Latest evaluation"}</span>
              <strong>{vi ? "Không phát hiện biến động tỷ lệ vi phạm bất thường." : "No unusual violation-rate movement detected."}</strong>
              <p>
                {historicalReady
                  ? (vi ? "Tỷ lệ vi phạm hiện tại vẫn nằm trong ngưỡng cơ sở lịch sử đã lưu." : "Current rule rates remain within their stored historical baselines.")
                  : vi
                    ? `Cần thêm ${Math.max(0, 6 - trends.length)} lượt chạy hoàn thành nữa để bật phát hiện z-score lịch sử.`
                    : `Collect ${Math.max(0, 6 - trends.length)} more completed run${6 - trends.length === 1 ? "" : "s"} to enable historical z-score detection.`}
              </p>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

function VisualizationPage({
  profile,
  results,
  anomalies,
  trends,
}: {
  profile: DatasetProfile | null;
  results: DqResult[];
  anomalies: DqAnomaly[];
  trends: QualityTrendPoint[];
}) {
  const { language } = useI18n();
  const vi = language === "vi";

  const latestScore =
    trends.at(-1)?.quality_score ?? profile?.validity_score ?? 0;
  const failedRules = results.filter(
    (result) => result.status === "FAIL",
  ).length;
  const previousScore = trends.at(-2)?.quality_score;
  const scoreDelta =
    previousScore === undefined ? null : latestScore - previousScore;
  const sortedColumns = [...(profile?.columns ?? [])]
    .sort((left, right) => right.null_rate - left.null_rate)
    .slice(0, 8);
  const maximumViolation = results.reduce((maximum, result) => {
    const rate = result.checked_count
      ? result.failed_count / result.checked_count
      : 0;
    return Math.max(maximum, rate);
  }, 0);
  const circumference = 2 * Math.PI * 52;
  const scoreOffset =
    circumference * (1 - Math.min(100, Math.max(0, latestScore)) / 100);
  const latestRunAt = trends.at(-1)?.created_at;
  return (
    <>
      <div className="page-heading visualization-heading">
        <div>
          <span className="eyebrow">{vi ? "PHÒNG ĐIỀU HÀNH CHẤT LƯỢNG" : "QUALITY CONTROL ROOM"}</span>
          <h1>{vi ? "Bảng quan sát chất lượng dữ liệu" : "Data quality observatory"}</h1>
          <p>
            {vi
              ? "Theo dõi sức khỏe lượt chạy, phát hiện độ trôi quy tắc và tập trung xem xét các tín hiệu cần chú ý."
              : "Monitor run health, surface rule drift, and focus review on the signals that need attention."}
          </p>
        </div>
        <div
          className="quality-dial"
          aria-label={`Latest quality score ${latestScore.toFixed(1)} percent`}
        >
          <svg viewBox="0 0 120 120" aria-hidden="true">
            <circle cx="60" cy="60" r="52" className="quality-dial-track" />
            <circle
              cx="60"
              cy="60"
              r="52"
              className="quality-dial-progress"
              strokeDasharray={circumference}
              strokeDashoffset={scoreOffset}
            />
          </svg>
          <div>
            <strong>{latestScore.toFixed(1)}</strong>
            <span>{vi ? "điểm chất lượng" : "quality score"}</span>
          </div>
        </div>
      </div>
      <section
        className="visual-kpi-rail"
        aria-label="Latest quality indicators"
      >
        <div>
          <span>{vi ? "Bản ghi đã profile" : "Profiled records"}</span>
          <strong>{(profile?.row_count ?? 0).toLocaleString()}</strong>
          <small>{vi ? "bộ dữ liệu hiện tại" : "current dataset"}</small>
        </div>
        <div>
          <span>{vi ? "Biến động mới nhất" : "Latest movement"}</span>
          <strong
            className={
              scoreDelta !== null && scoreDelta < 0 ? "metric-warn" : ""
            }
          >
            {scoreDelta === null
              ? (vi ? "Điểm cơ sở" : "Baseline")
              : `${scoreDelta >= 0 ? "+" : ""}${scoreDelta.toFixed(2)} pts`}
          </strong>
          <small>
            {vi ? `${trends.length} lượt chạy hoàn thành` : `${trends.length} completed ${trends.length === 1 ? "run" : "runs"}`}
          </small>
        </div>
        <div>
          <span>{vi ? "Quy tắc cần xem xét" : "Rules requiring review"}</span>
          <strong className={failedRules ? "metric-warn" : ""}>
            {failedRules} / {results.length}
          </strong>
          <small>{vi ? `${(maximumViolation * 100).toFixed(1)}% vi phạm cao nhất` : `${(maximumViolation * 100).toFixed(1)}% peak violation`}</small>
        </div>
        <div>
          <span>{vi ? "Trạng thái tín hiệu" : "Signal status"}</span>
          <strong className={anomalies.length ? "metric-warn" : ""}>
            {anomalies.length ? (vi ? "Cần chú ý" : "Attention") : (vi ? "Ổn định" : "Stable")}
          </strong>
          <small>
            {vi ? `${anomalies.length} bất thường phát hiện` : `${anomalies.length} detected ${anomalies.length === 1 ? "anomaly" : "anomalies"}`}
          </small>
        </div>
      </section>
      <section className="visual-grid">
        <article className="panel trend-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{vi ? "LỊCH SỬ CHẠY" : "RUN HISTORY"}</span>
              <h3>{vi ? "Xu hướng điểm chất lượng" : "Quality score trend"}</h3>
            </div>
            <span className="panel-caption">
              {latestRunAt
                ? (vi ? `Cập nhật lúc ${formatTime(latestRunAt)}` : `Updated ${formatTime(latestRunAt)}`)
                : (vi ? "Chưa có lượt chạy" : "No completed run")}
            </span>
          </div>
          <TrendChart points={trends} />
          <div className="chart-legend">
            <span>
              <i />
              {vi ? "Điểm chất lượng" : "Quality score"}
            </span>
            <small>{vi ? "Tính toán từ kết quả các quy tắc kiểm tra" : "Calculated from bounded rule results"}</small>
          </div>
        </article>
        <article className="panel signal-summary">
          <div className="signal-heading">
            <span className="eyebrow">{vi ? "TÍN HIỆU MỚI NHẤT" : "LATEST SIGNALS"}</span>
            <span
              className={`signal-state ${anomalies.length ? "attention" : "stable"}`}
            >
              {anomalies.length ? (vi ? "Cần duyệt" : "Review") : (vi ? "Ổn định" : "Stable")}
            </span>
          </div>
          <div className="signal-number">
            <strong>{anomalies.length}</strong>
            <span>{vi ? "bất thường phát hiện" : "anomalies detected"}</span>
          </div>
          <div className="signal-row">
            <span>{vi ? "Quy tắc vi phạm" : "Failed rules"}</span>
            <strong>{failedRules}</strong>
          </div>
          <div className="signal-row">
            <span>{vi ? "Quy tắc khả dụng" : "Checks available"}</span>
            <strong>{results.length}</strong>
          </div>
          <div className="signal-row">
            <span>{vi ? "Chế độ phát hiện" : "Detection mode"}</span>
            <strong>
              {anomalies[0]?.detection_mode === "HISTORICAL"
                ? (vi ? "Theo lịch sử" : "Historical")
                : (vi ? "Khởi đầu lạnh" : "Cold start")}
            </strong>
          </div>
          <p className="signal-insight">
            {anomalies[0]?.reason ??
              (vi ? "Không phát hiện biến động tỷ lệ vi phạm bất thường trong lượt chạy gần nhất." : "No abnormal violation-rate movement detected in the latest completed run.")}
          </p>
        </article>
        <article className="panel completeness-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{vi ? "SỨC KHỎE HỒ SƠ DỮ LIỆU" : "PROFILE HEALTH"}</span>
              <h3>{vi ? "Độ đầy đủ theo cột" : "Column completeness"}</h3>
            </div>
            <span className="panel-caption">{vi ? "sắp xếp từ độ bao phủ thấp nhất" : "lowest coverage first"}</span>
          </div>
          <div className="viz-bars">
            {sortedColumns.map((column) => {
              const completeness = Math.max(0, 100 - column.null_rate * 100);
              return (
                <div className="viz-bar-row" key={column.name}>
                  <span>{column.name}</span>
                  <div>
                    <i style={{ width: `${completeness}%` }} />
                  </div>
                  <strong>{completeness.toFixed(1)}%</strong>
                </div>
              );
            })}
            {!profile && (
              <div className="chart-empty">
                {vi ? "Tạo profile dữ liệu để xem biểu đồ độ đầy đủ." : "Create a dataset profile to visualize completeness."}
              </div>
            )}
          </div>
        </article>
        <article className="panel failure-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{vi ? "THỰC THI QUY TẮC" : "RULE EXECUTION"}</span>
              <h3>{vi ? "Tỷ lệ vi phạm" : "Violation rates"}</h3>
            </div>
            <span className="panel-caption">{vi ? "lượt chạy hoàn thành mới nhất" : "latest completed run"}</span>
          </div>
          <div className="failure-list">
            {results.map((result) => {
              const rate = result.checked_count
                ? result.failed_count / result.checked_count
                : 0;
              return (
                <div className="failure-item" key={result.rule_id}>
                  <div className="failure-copy">
                    <strong title={result.rule_title}>
                      {result.rule_title}
                    </strong>
                    <span>
                      {vi
                        ? `${result.failed_count.toLocaleString()} / ${result.checked_count.toLocaleString()} dòng`
                        : `${result.failed_count.toLocaleString()} of ${result.checked_count.toLocaleString()} rows`}
                    </span>
                  </div>
                  <strong className={rate ? "metric-warn" : ""}>
                    {(rate * 100).toFixed(2)}%
                  </strong>
                  <div className="failure-track">
                    <i style={{ width: `${Math.min(100, rate * 100)}%` }} />
                  </div>
                </div>
              );
            })}
            {!results.length && (
              <div className="chart-empty">{vi ? "Chưa có kết quả quy tắc nào được lưu." : "No persisted rule results yet."}</div>
            )}
          </div>
        </article>
        <AnomalyMonitoringPanel anomalies={anomalies} trends={trends} />
      </section>
    </>
  );
}

function rowHasQualityIssue(row: DatasetRow) {
  return (
    (row.trip_distance ?? 0) < 0 ||
    (row.fare_amount ?? 0) < 0 ||
    Boolean(row.payment_type?.startsWith("Invalid")) ||
    Boolean(
      row.pickup_at && row.dropoff_at && row.pickup_at > row.dropoff_at,
    ) ||
    !row.vendor_id
  );
}

function AdminPage({
  users,
  access,
  loading,
  onCreate,
  onUpdate,
  onGrant,
  onRevoke,
}: {
  users: UserAccount[];
  access: DatasetAccess[];
  loading: boolean;
  onCreate: (input: UserCreateInput) => void;
  onUpdate: (username: string, input: UserUpdateInput) => void;
  onGrant: (username: string, level: DatasetAccessLevel) => void;
  onRevoke: (username: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("USER");
  const [grantUsername, setGrantUsername] = useState("");
  const [grantLevel, setGrantLevel] = useState<DatasetAccessLevel>("READ");
  const grantedNames = new Set(access.map((item) => item.username));
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">ADMINISTRATION</span>
          <h1>Accounts and access</h1>
          <p>
            Provision local demo users and grant read or manage access to the
            registered dataset.
          </p>
        </div>
      </div>
      <div className="admin-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">ACCOUNT DIRECTORY</span>
              <h3>Local users</h3>
            </div>
            <span className="panel-caption">{users.length} accounts</span>
          </div>
          <form
            className="admin-form"
            onSubmit={(event) => {
              event.preventDefault();
              onCreate({ username, display_name: displayName, password, role });
              setUsername("");
              setDisplayName("");
              setPassword("");
            }}
          >
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              placeholder="username"
              required
            />
            <input
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="display name"
              required
            />
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="password (8+ chars)"
              type="password"
              minLength={8}
              required
            />
            <select
              value={role}
              onChange={(event) => setRole(event.target.value as UserRole)}
            >
              <option>USER</option>
              <option>STEWARD</option>
              <option>ADMIN</option>
            </select>
            <button className="button primary">Create account</button>
          </form>
          <div className="admin-list">
            {loading ? (
              <div className="table-empty">Loading accounts…</div>
            ) : (
              users.map((user) => (
                <AdminUserRow key={user.id} user={user} onUpdate={onUpdate} />
              ))
            )}
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">DATASET ACCESS</span>
              <h3>Registered artifact</h3>
            </div>
            <span className="panel-caption">{access.length} grants</span>
          </div>
          <form
            className="admin-form grant"
            onSubmit={(event) => {
              event.preventDefault();
              if (grantUsername) onGrant(grantUsername, grantLevel);
            }}
          >
            <select
              value={grantUsername}
              onChange={(event) => setGrantUsername(event.target.value)}
              required
            >
              <option value="">Select account</option>
              {users
                .filter((user) => !grantedNames.has(user.username))
                .map((user) => (
                  <option key={user.username} value={user.username}>
                    {user.username} · {user.role}
                  </option>
                ))}
            </select>
            <select
              value={grantLevel}
              onChange={(event) =>
                setGrantLevel(event.target.value as DatasetAccessLevel)
              }
            >
              <option value="READ">Read</option>
              <option value="MANAGE">Manage</option>
            </select>
            <button className="button primary">Grant access</button>
          </form>
          <div className="admin-list">
            {access.map((grant) => (
              <div className="admin-row" key={grant.id}>
                <div>
                  <strong>{grant.display_name}</strong>
                  <small>
                    {grant.username} · {grant.role}
                  </small>
                </div>
                <span className="status-pill info">
                  <span className="status-dot" />
                  {grant.access_level}
                </span>
                <button
                  className="button ghost"
                  onClick={() => onRevoke(grant.username)}
                >
                  Revoke
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}

function AdminUserRow({
  user,
  onUpdate,
}: {
  user: UserAccount;
  onUpdate: (username: string, input: UserUpdateInput) => void;
}) {
  const [role, setRole] = useState<UserRole>(user.role);
  const [status, setStatus] = useState(user.status);
  return (
    <div className="admin-row">
      <div>
        <strong>{user.display_name}</strong>
        <small>
          {user.username} · created {formatTime(user.created_at)}
        </small>
      </div>
      <select
        value={role}
        onChange={(event) => setRole(event.target.value as UserRole)}
      >
        <option>USER</option>
        <option>STEWARD</option>
        <option>ADMIN</option>
      </select>
      <select
        value={status}
        onChange={(event) => setStatus(event.target.value as typeof status)}
      >
        <option>ACTIVE</option>
        <option>SUSPENDED</option>
        <option>DISABLED</option>
      </select>
      <button
        className="button ghost"
        onClick={() => onUpdate(user.username, { role, status })}
      >
        Save
      </button>
    </div>
  );
}

function AuditPage({ logs }: { logs: AuditLog[] }) {
  const { t, language } = useI18n();
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">APPEND-ONLY HISTORY</span>
          <h1>Audit history</h1>
          <p>
            Every state transition and execution remains observable for the
            Steward.
          </p>
        </div>
        <StatusPill label="AUDIT ENABLED" tone="success" />
      </div>
      <div className="panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">EVENT STREAM</span>
            <h3>Recent activity</h3>
          </div>
          <span className="panel-caption">{logs.length} events</span>
        </div>
        {logs.length ? (
          <div className="audit-list">
            {logs.map((log) => {
              let summary = log.summary;
              let action = log.action;
              let entity = log.entity_type;

              if (language === "vi") {
                const summaryMap: Record<string, string> = {
                  "Started ingestion job": "Bắt đầu tiến trình nạp dữ liệu",
                  "Generated dataset profile": "Đã tạo hồ sơ dữ liệu",
                  "Proposed rules generated": "Đã sinh đề xuất quy tắc",
                  "Rules generated": "Đã sinh quy tắc",
                  "Data quality rules executed": "Đã chạy kiểm tra chất lượng dữ liệu",
                  "Workflow created": "Tạo luồng công việc mới",
                  "Rule checks started": "Bắt đầu kiểm tra quy tắc",
                  "Rule checks finished": "Hoàn tất kiểm tra quy tắc",
                  "Rule checks failed": "Kiểm tra quy tắc thất bại",
                };

                // Try exact match or partial match
                for (const [en, vi] of Object.entries(summaryMap)) {
                  if (summary.includes(en)) {
                    summary = summary.replace(en, vi);
                    break;
                  }
                }

                // Fallbacks for dynamic summaries
                if (summary.includes("Proposed")) summary = summary.replace("Proposed", "Đề xuất");
                if (summary.includes("rule")) summary = summary.replace("rule", "quy tắc");
                if (summary.includes("Rule")) summary = summary.replace("Rule", "Quy tắc");
                if (summary.includes("accepted")) summary = summary.replace("accepted", "được chấp nhận");
                if (summary.includes("rejected")) summary = summary.replace("rejected", "bị từ chối");
                if (summary.includes("created")) summary = summary.replace("created", "được tạo");
                if (summary.includes("deleted")) summary = summary.replace("deleted", "đã bị xóa");
                if (summary.includes("updated")) summary = summary.replace("updated", "đã cập nhật");
                if (summary.includes("Executed")) summary = summary.replace("Executed", "Đã thực thi");

                const actionMap: Record<string, string> = {
                  "CREATE": "TẠO",
                  "UPDATE": "CẬP NHẬT",
                  "DELETE": "XÓA",
                  "EXECUTE": "THỰC THI",
                  "APPROVE": "PHÊ DUYỆT",
                  "REJECT": "TỪ CHỐI"
                };
                action = actionMap[action] || action;

                const entityMap: Record<string, string> = {
                  "DATASET": "TẬP DỮ LIỆU",
                  "WORKFLOW_JOB": "TÁC VỤ LUỒNG",
                  "WORKFLOW_RUN": "LẦN CHẠY LUỒNG",
                  "PROPOSAL": "ĐỀ XUẤT",
                  "RULE": "QUY TẮC",
                  "RUN": "LẦN CHẠY",
                  "PROFILE": "HỒ SƠ DỮ LIỆU"
                };
                entity = entityMap[entity] || entity;
              }

              return (
                <div className="audit-row" key={log.id}>
                  <div className="audit-icon">✓</div>
                  <div>
                    <strong>{summary}</strong>
                    <span>
                      {action} · {entity} · {log.actor}
                    </span>
                  </div>
                  <time>{formatTime(log.created_at)}</time>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="table-empty">No audit events yet.</div>
        )}
      </div>
    </>
  );
}

function RuleSpecEditor({
  rule,
  onChange,
}: {
  rule: RuleSpec;
  onChange: (rule: RuleSpec) => void;
}) {
  const update = (patch: Partial<RuleSpec>) => onChange({ ...rule, ...patch });
  const csv = (values: string[] | undefined) => (values ?? []).join(", ");
  const parseCsv = (value: string) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  return (
    <div className="rule-editor">
      <span className="eyebrow">TYPED RULE PARAMETERS</span>
      <div className="rule-type-readonly">
        <strong>{rule.type.replaceAll("_", " ")}</strong>
        <code>{formatRule(rule)}</code>
      </div>
      {rule.type === "not_null" && (
        <label>
          Column
          <input
            value={rule.column ?? ""}
            onChange={(event) => update({ column: event.target.value })}
          />
        </label>
      )}
      {rule.type === "numeric_range" && (
        <>
          <label>
            Column
            <input
              value={rule.column ?? ""}
              onChange={(event) => update({ column: event.target.value })}
            />
          </label>
          <div className="dialog-fields">
            <label>
              Minimum
              <input
                type="number"
                value={rule.min_value ?? ""}
                onChange={(event) =>
                  update({
                    min_value:
                      event.target.value === ""
                        ? undefined
                        : Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              Maximum
              <input
                type="number"
                value={rule.max_value ?? ""}
                onChange={(event) =>
                  update({
                    max_value:
                      event.target.value === ""
                        ? undefined
                        : Number(event.target.value),
                  })
                }
              />
            </label>
          </div>
        </>
      )}
      {rule.type === "accepted_values" && (
        <>
          <label>
            Column
            <input
              value={rule.column ?? ""}
              onChange={(event) => update({ column: event.target.value })}
            />
          </label>
          <label>
            Allowed values
            <input
              value={csv(rule.allowed_values)}
              onChange={(event) =>
                update({ allowed_values: parseCsv(event.target.value) })
              }
            />
          </label>
        </>
      )}
      {rule.type === "cross_field_comparison" && (
        <>
          <label>
            Columns
            <input
              value={csv(rule.columns)}
              onChange={(event) =>
                update({ columns: parseCsv(event.target.value) })
              }
            />
          </label>
          <label>
            Operator
            <select
              value={rule.operator ?? "≤"}
              onChange={(event) => update({ operator: event.target.value })}
            >
              <option value="≤">≤</option>
              <option value="<">&lt;</option>
              <option value=">=">≥</option>
              <option value=">">&gt;</option>
            </select>
          </label>
        </>
      )}
      {rule.type === "duplicate_fingerprint" && (
        <label>
          Fingerprint columns
          <input
            value={csv(rule.fingerprint_columns)}
            onChange={(event) =>
              update({ fingerprint_columns: parseCsv(event.target.value) })
            }
          />
        </label>
      )}
    </div>
  );
}

function ManualRuleDialog({
  onClose,
  onSave,
}: {
  onClose: () => void;
  onSave: (input: ManualRuleInput) => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [severity, setSeverity] = useState<RuleProposal["severity"]>("MEDIUM");
  const [type, setType] = useState<RuleSpec["type"]>("not_null");
  const [rule, setRule] = useState<RuleSpec>({ type: "not_null" });
  const changeType = (next: RuleSpec["type"]) => {
    setType(next);
    setRule({ type: next });
  };
  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="dialog" role="dialog" aria-modal="true">
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">DATA STEWARD AUTHORING</span>
            <h2>Add manual rule</h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close dialog"
          >
            ×
          </button>
        </div>
        <p className="muted">
          Create a typed rule without waiting for the Agent. It enters the
          review queue and must be approved before execution.
        </p>
        <label>
          Title
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="e.g. Pickup location must be known"
          />
        </label>
        <label>
          Description
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            placeholder="Explain the quality expectation"
          />
        </label>
        <label>
          Severity
          <select
            value={severity}
            onChange={(event) =>
              setSeverity(event.target.value as RuleProposal["severity"])
            }
          >
            <option>LOW</option>
            <option>MEDIUM</option>
            <option>HIGH</option>
          </select>
        </label>
        <label>
          Rule type
          <select
            value={type}
            onChange={(event) =>
              changeType(event.target.value as RuleSpec["type"])
            }
          >
            <option value="not_null">Not null</option>
            <option value="numeric_range">Numeric range</option>
            <option value="accepted_values">Accepted values</option>
            <option value="cross_field_comparison">
              Cross-field comparison
            </option>
            <option value="duplicate_fingerprint">Duplicate fingerprint</option>
          </select>
        </label>
        <RuleSpecEditor rule={rule} onChange={setRule} />
        <div className="dialog-actions">
          <button className="button ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button primary"
            disabled={!title.trim() || !description.trim()}
            onClick={() => onSave({ title, description, severity, rule })}
          >
            Create rule
          </button>
        </div>
      </section>
    </div>
  );
}

function EditDialog({
  proposal,
  onClose,
  onSave,
}: {
  proposal: RuleProposal;
  onClose: () => void;
  onSave: (input: {
    title: string;
    description: string;
    severity: RuleProposal["severity"];
    rule: RuleSpec;
  }) => void;
}) {
  const [title, setTitle] = useState(proposal.title);
  const [description, setDescription] = useState(proposal.description);
  const [severity, setSeverity] = useState(proposal.severity);
  const [rule, setRule] = useState<RuleSpec>({
    ...proposal.rule,
    columns: proposal.rule.columns ? [...proposal.rule.columns] : undefined,
    allowed_values: proposal.rule.allowed_values
      ? [...proposal.rule.allowed_values]
      : undefined,
    fingerprint_columns: proposal.rule.fingerprint_columns
      ? [...proposal.rule.fingerprint_columns]
      : undefined,
  });
  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="dialog" role="dialog" aria-modal="true">
        <div className="dialog-heading">
          <div>
            <span className="eyebrow">HITL REVIEW</span>
            <h2>Edit proposal</h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close dialog"
          >
            ×
          </button>
        </div>
        <p className="muted">
          Edit the typed specification and metadata. The server remains
          responsible for validation and compilation.
        </p>
        <label>
          Title
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          Description
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={4}
          />
        </label>
        <label>
          Severity
          <select
            value={severity}
            onChange={(event) =>
              setSeverity(event.target.value as RuleProposal["severity"])
            }
          >
            <option>LOW</option>
            <option>MEDIUM</option>
            <option>HIGH</option>
          </select>
        </label>
        <RuleSpecEditor rule={rule} onChange={setRule} />
        <div className="dialog-actions">
          <button className="button ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button primary"
            onClick={() => onSave({ title, description, severity, rule })}
          >
            Save edit
          </button>
        </div>
      </section>
    </div>
  );
}

export default App;
