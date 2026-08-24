import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, isMockMode, workflowApi } from "./api";
import { ApiError, clearApiSession } from "./api/client";
import ThemeControl from "./ThemeControl";
import LanguageToggle from "./LanguageToggle";
import { useI18n } from "./i18n/context";
import { Step5Analytics } from "./components/wizard/Step5Analytics";
import { Graph1Studio } from "./features/graph1/Graph1Studio";
import { Graph1DetailsSidebar } from "./features/graph1/Graph1DetailsSidebar";
import { StagePresenter, buildDisplayStages } from "./features/graph1/presenters";
import { AnalysisStudio } from "./features/analysis/AnalysisStudio";
import { PanelRightOpen } from "lucide-react";
import type {
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
  ManualRuleInput,
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
  Graph1Run,
  Graph1NodeExecution,
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
  | "admin";

const sleep = (duration: number) =>
  new Promise((resolve) => window.setTimeout(resolve, duration));

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatRule(rule?: RuleSpec | null) {
  if (!rule || !rule.type) return "CUSTOM";
  if (rule.type === "not_null") return `NOT NULL · ${rule.column ?? ""}`;
  if (rule.type === "numeric_range")
    return `RANGE · ${rule.column ?? ""} ≥ ${rule.min_value ?? 0}`;
  if (rule.type === "accepted_values")
    return `VALUES · ${rule.column ?? ""} ∈ ${(rule.allowed_values ?? []).join(", ")}`;
  if (rule.type === "cross_field_comparison")
    return `COMPARE · ${(rule.columns ?? []).join(` ${rule.operator ?? "≤"} `)}`;
  return `DUPLICATE · ${(rule.fingerprint_columns ?? []).join(" + ")}`;
}

function getErrorMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiError))
    return error instanceof TypeError
      ? "Cannot reach the API service. Confirm that the local backend is running, then try again."
      : error instanceof Error
        ? error.message
        : fallback;
  if (error.status === 401)
    return "Your session has expired. Please sign in again.";
  if (error.status === 409)
    return (
      error.message || "The workflow cannot continue from its current state."
    );
  if (error.status === 422)
    return "The request is not valid for the current workflow state.";
  if (error.status === 429)
    return "The demo quota has been reached. Please try again later.";
  if (error.status >= 500)
    return "The service is temporarily unavailable. Retry when it is ready.";
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

function ProgressPanel({ job, title }: { job: Job; title: string }) {
  return (
    <div className="progress-toast">
      <div className="progress-toast-header">
        <div className="progress-toast-title">
          <span className="spinner" />
          <strong>{title}</strong>
        </div>
        <span className="progress-toast-percent">{job.progress}%</span>
      </div>
      <div className="progress-track" style={{ height: "6px", margin: "4px 0" }}>
        <span style={{ width: `${job.progress}%` }} />
      </div>
      <div className="progress-toast-footer">
        <span className="progress-toast-msg">{job.message}</span>
        <span className="progress-toast-status">{job.status}</span>
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
          <span className="eyebrow">HỆ THỐNG TRÍ TUỆ GIÁM SÁT CHẤT LƯỢNG DỮ LIỆU</span>
          <h1>
            Biến tín hiệu dữ liệu thành <span>quyết định đáng tin cậy.</span>
          </h1>
          <p>
            Phân tích các tập dữ liệu, xem xét các quy tắc dựa trên bằng chứng minh bạch và chỉ thực thi những kiểm thử được phê duyệt.
          </p>
          <div className="metric-row">
            <div>
              <strong>LIVE</strong>
              <span>dữ liệu Supabase trực tiếp</span>
            </div>
            <div>
              <strong>5</strong>
              <span>mẫu quy tắc chuẩn</span>
            </div>
            <div>
              <strong>100%</strong>
              <span>minh bạch nhật ký kiểm toán</span>
            </div>
          </div>
        </div>
        <div className="login-footer">GATE 2 · DỰ ÁN HỆ THỐNG AI DATA QUALITY</div>
      </div>
      <section className="login-card">
        <div className="mobile-brand">
          <span className="brand-mark">RP</span> RidePulse <em>DQ</em>
        </div>
        <span className="eyebrow">TRUY CẬP THEO VAI TRÒ</span>
        <h2>Chào mừng trở lại</h2>
        <p className="muted">
          Đăng nhập bằng tài khoản của bạn để vào không gian làm việc.
        </p>
        <form onSubmit={submit}>
          <label htmlFor="username">Tên đăng nhập</label>
          <input
            id="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="user, steward, hoặc admin"
            autoFocus
          />
          <label htmlFor="password">Mật khẩu</label>
          <input
            id="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Nhập mật khẩu"
          />
          {error && <div className="inline-error">{error}</div>}
          <button
            className="button primary full"
            disabled={busy || username.length < 1 || password.length < 1}
          >
            {busy ? "Đang mở không gian làm việc…" : "Vào không gian làm việc →"}
          </button>
        </form>
        <div className="login-note">
          <span className="lock-icon">⌁</span>
          <span>
            <strong>Tài khoản dùng thử</strong>
            <br />
            Thông tin đăng nhập được cấp riêng cho người trình diễn. Không có
            mật khẩu mặc định trên giao diện.
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

function workflowPhaseIndex(step: WorkflowStepKey) {
  return workflowPhases.findIndex((phase) => phase.steps.includes(step));
}

function DatasetsPage({
  datasets,
  dataset,
  onOpenExplorer,
  onImportDataset,
  onSelectDataset,
  onDeleteDataset,
  onStartUnderstand,
  canOperate,
  importing,
  busy,
  graph1Run,
  graph1Nodes,
  onViewNodeDetails,
  showNodeDetails,
  onRefreshGraph1,
}: {
  datasets: Dataset[];
  dataset?: Dataset;
  onOpenExplorer: (datasetId: string) => void;
  onImportDataset: (file: File) => void;
  onSelectDataset: (datasetId: string) => void;
  onDeleteDataset?: (datasetId: string) => void;
  onStartUnderstand: (datasetId: string) => void;
  canOperate: boolean;
  importing: boolean;
  busy: boolean;
  graph1Run: Graph1Run | null;
  graph1Nodes: Graph1NodeExecution[];
  onViewNodeDetails: (datasetId: string) => void;
  showNodeDetails: boolean;
  onRefreshGraph1: (runId: string) => Promise<unknown>;
}) {
  const { t, language } = useI18n();

  const formatDatasetStatus = (status: string) => {
    const clean = status.replaceAll("_", " ").toLowerCase();
    if (clean.includes("profile ready")) return language === "vi" ? "SẴN SÀNG" : "PROFILE READY";
    if (clean.includes("ingested")) return language === "vi" ? "ĐÃ NẠP" : "INGESTED";
    if (clean.includes("registered")) return language === "vi" ? "ĐÃ ĐĂNG KÝ" : "REGISTERED";
    return status.replaceAll("_", " ").toUpperCase();
  };

  const hasWorkflowResults = Boolean(graph1Run || graph1Nodes.length);
  const displayStages = useMemo(() => buildDisplayStages(graph1Nodes), [graph1Nodes]);
  const stageFor = (key: string) => displayStages.find((stage) => stage.key === key);
  const agentDisabledReason = !canOperate
    ? "Run Agent Workflow requires a Steward or Admin session. Sign in with a steward account to start it."
    : importing
      ? "Wait for the dataset import and profile to finish before starting the Agent Workflow."
      : busy
        ? "The Agent Workflow request is being prepared. Please wait a moment."
        : dataset?.status !== "PROFILE_READY"
          ? "The dataset profile must be ready before the Agent Workflow can run."
          : null;

  return (
    <div className="datasets-page">
      <div className="page-heading datasets-heading">
        <div>
          <span className="eyebrow">STEP 1 · {t("wizard.step1Title").toUpperCase()}</span>
          <h1 style={{ whiteSpace: "nowrap", maxWidth: "none" }}>{t("datasets.step1Title")}</h1>
          <p style={{ whiteSpace: "nowrap" }}>{t("datasets.step1Subtitle")}</p>
        </div>
      </div>

      {datasets.length ? (
        <div className="dataset-catalog-grid">
          <label className={`dataset-import-card ${importing ? "busy" : ""}`}>
            <input type="file" accept=".csv,.parquet,text/csv,application/vnd.apache.parquet" disabled={!canOperate || importing} onChange={(event) => { const file = event.target.files?.[0]; if (file) onImportDataset(file); event.currentTarget.value = ""; }} />
            <span className="dataset-import-plus">+</span>
            <strong>{importing ? t("datasets.profiling") : t("datasets.import")}</strong>
            <small>{t("datasets.importSub")}</small>
          </label>
          {datasets.map((item) => {
            const isSelected = item.id === dataset?.id;
            return (
              <article
                className={`dataset-catalog-card ${isSelected ? "active" : ""}`}
                key={item.id}
                onClick={() => onSelectDataset(item.id)}
                style={{ cursor: "pointer", border: isSelected ? "2px solid var(--color-primary, #2563eb)" : undefined }}
              >
                <div className="dataset-catalog-top">
                  <StatusPill
                    label={isSelected ? t("datasets.selected") : formatDatasetStatus(item.status)}
                    tone={isSelected ? "success" : "info"}
                  />
                  <code>{item.manifest_version}</code>
                </div>
                <h2>{item.name}</h2>
                <p>{item.description}</p>
                <div className="dataset-catalog-stats">
                  <div>
                    <span>{t("datasets.rows")}</span>
                    <strong>{item.row_count.toLocaleString()}</strong>
                  </div>
                  <div>
                    <span>{t("datasets.source")}</span>
                    <strong>{item.source_label}</strong>
                  </div>
                  <div>
                    <span>{t("datasets.updated")}</span>
                    <strong>{formatTime(item.updated_at)}</strong>
                  </div>
                </div>
                <div className="dataset-catalog-actions" style={{ display: "flex", justifyContent: "space-between", gap: "12px", marginTop: "12px" }}>
                  {isSelected && canOperate && item.status === "PROFILE_READY" && (
                    <button
                      type="button"
                      className="button primary"
                      disabled={busy || importing}
                      onClick={(event) => {
                        event.stopPropagation();
                        onStartUnderstand(item.id);
                      }}
                    >
                      Generate Rules →
                    </button>
                  )}
                  <button
                    type="button"
                    className="button ghost"
                    style={{ color: "#dc2626", borderColor: "#fca5a5" }}
                    onClick={(e) => {
                      e.stopPropagation();
                      if (window.confirm(t("datasets.confirmDelete", { name: item.name }))) {
                        onDeleteDataset?.(item.id);
                      }
                    }}
                    title={t("datasets.delete")}
                  >
                    🗑️ {t("datasets.delete")}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className="empty-state">
          <h2>{t("datasets.noDatasets")}</h2>
          <p className="muted">
            {t("datasets.noDatasetsDesc")}
          </p>
        </div>
      )}

      {/* Understand Data Agent Output Card */}
      {false && dataset && (
        <section className="panel" style={{ marginTop: "24px", padding: "24px" }}>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("datasets.agentCapability")}</span>
        <h2>{t("datasets.understandAgentTitle", { name: dataset!.name })}</h2>
              <p className="muted">{t("datasets.understandAgentDesc")}</p>
            </div>
            <button
              type="button"
              className="button primary"
              disabled={Boolean(agentDisabledReason)}
              title={agentDisabledReason ?? "Generate the Data Dictionary, Semantic Contract, and Rule Proposal."}
              aria-describedby={agentDisabledReason ? "agent-workflow-disabled-reason" : undefined}
              onClick={() => onStartUnderstand(dataset!.id)}
            >
              {busy ? "Starting Agent…" : "Run Agent Workflow →"}
            </button>
            {graph1Run && (
              <button
                id="graph1-details-trigger"
                type="button"
                className={`button secondary ${showNodeDetails ? "active" : ""}`}
                aria-controls="graph1-details-sidebar"
                aria-expanded={showNodeDetails}
                onClick={() => onViewNodeDetails(dataset!.id)}
              >
                <PanelRightOpen aria-hidden="true" />
                {showNodeDetails ? "Hide node details" : "View node details"}
              </button>
            )}
          </div>

          {hasWorkflowResults ? (
            <div className="understanding-holder" style={{ marginTop: "16px" }}>
              <div className="understanding-summary" style={{ padding: "16px", background: "var(--surface-muted, #f8fafc)", borderRadius: "8px", borderLeft: "4px solid var(--accent, #2563eb)" }}>
                <span className="eyebrow">AGENT WORKFLOW · {String(graph1Run?.status ?? "RUNNING").replaceAll("_", " ")}</span>
                <p style={{ marginTop: "8px", fontSize: "15px", lineHeight: "1.5", color: "var(--ink)" }}>Results below are read from persisted Agent Workflow node outputs.</p>
              </div>
              <div className="workflow-result-chain">
                {[
                  ["Data Dictionary", "data_dictionary_generator"],
                  ["Semantic Contract", "understanding_semantic"],
                  ["Rule Proposal", "rule_proposer"],
                ].map(([label, key]) => {
                  const stage = stageFor(String(key));
                  return <section className="panel" key={String(label)}>
                    <div className="workflow-artifact-heading"><div><span className="eyebrow">{String(label)}</span><strong>{stage?.description ?? "Persisted Agent Workflow output"}</strong></div><span className={`g1-chip ${stage?.status === "SUCCEEDED" ? "success" : stage?.status === "FAILED" ? "danger" : "warning"}`}>{stage?.status ? stage.status.replaceAll("_", " ") : "PENDING"}</span></div>
                    <div className="workflow-artifact-content">{stage ? <StagePresenter stage={stage} /> : <div className="workflow-artifact-empty-inline">Waiting for persisted output…</div>}</div>
                  </section>
                })}
              </div>
            </div>
          ) : (
            <div className="workflow-artifact-empty" style={{ marginTop: "16px", padding: "20px", background: "var(--color-bg-subtle, #f8fafc)", borderRadius: "8px", textAlign: "center" }}>
              Run Agent Workflow to generate the Data Dictionary, Semantic Contract, and Rule Proposal.
            </div>
          )}
          {agentDisabledReason && (
            <p
              id="agent-workflow-disabled-reason"
              className="muted"
              role="status"
              style={{ marginTop: "12px" }}
            >
              {agentDisabledReason}
            </p>
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
}) {
  const { t } = useI18n();
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
            <span className="eyebrow">{t("workflow.eyebrow")}</span>
            <h1>{t("workflow.title")}</h1>
            <p>
              {selectedRuleDataset?.name ?? t("workflow.selectDataset")} ·{" "}
              {t("workflow.subtitle")}
            </p>
          </div>
          <span className="status-pill">
            {selectedRuleDataset ? t("workflow.ready") : t("workflow.selectDataset")}
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
                  <strong>{index === 0 ? t("workflow.phase1Label") : t("workflow.phase2Label")}</strong>
                  <span>{index === 0 ? t("workflow.phase1Owner") : t("workflow.phase2Owner")}</span>
                </div>
              </button>
            ))}
          </aside>
          <section className="workflow-detail panel workflow-selection-detail">
            <div className="workflow-detail-heading">
              <div>
                <span className="eyebrow">{t("workflow.step0Eyebrow")}</span>
                <h2>{t("workflow.step0Title")}</h2>
                <p>{t("workflow.step0Subtitle")}</p>
              </div>
              <span className="status-pill">
                {selectedRuleDataset ? t("workflow.datasetSelected") : t("workflow.ready")}
              </span>
            </div>
            <div className="dataset-selection-holder">
              <div className="section-heading">
                <div>
                  <span className="eyebrow">{t("workflow.registeredInputs")}</span>
                  <h3>{t("workflow.selectInput")}</h3>
                </div>
                <span className="muted">{datasets.length} {t("workflow.available")}</span>
              </div>
              <div className="dataset-choice-list">
                <label className="dataset-choice dataset-choice-import">
                  <input type="file" accept=".csv,.parquet,text/csv,application/vnd.apache.parquet" disabled={!canOperate || busy} onChange={(event) => { const file = event.target.files?.[0]; if (file) onUploadPreview(file); event.currentTarget.value = ""; }} />
                  <span className="dataset-choice-import-icon">+</span>
                  <span><strong>{t("datasets.import")}</strong><small>CSV or Parquet · profile automatically</small></span>
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
                {t("workflow.startRuleProposer")} <span aria-hidden="true">→</span>
              </button>
              <small>
                {t("workflow.proposerNotice")}
              </small>
              {!canOperate && (
                <small>{t("workflow.stewardRequired")}</small>
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
        <span className="eyebrow">{t("workflow.eyebrow")}</span>
        <h2>{t("workflow.noDatasetSelected")}</h2>
        <p className="muted">
          {t("workflow.noDatasetSelectedDesc")}
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
                {profile ? `${profile!.completeness_score.toFixed(1)}%` : "—"}
              </strong>
            </div>
            <div>
              <span>Validity</span>
              <strong>
                {profile ? `${profile!.validity_score.toFixed(1)}%` : "—"}
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
              <span className="muted">{contractColumns.length} mapped</span>
            </div>
            <div className="schema-list">
              {contractColumns.map((column) => (
                <div className="schema-row" key={String(column.name)}>
                  <strong>{String(column.name ?? "Unnamed column")}</strong>
                  <span>{String(column.semantic_type ?? "unknown")}</span>
                  <small>
                    {typeof column.confidence === "number"
                      ? `${Math.round(column.confidence * 100)}% confidence`
                      : "No confidence score"}
                  </small>
                </div>
              ))}
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
              {Array.isArray(payload.evidence) &&
                payload.evidence.map((evidence) => (
                  <span key={String(evidence)} className="evidence-chip">
                    {String(evidence)}
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
    if (artifact.type === "DQ_RUN")
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
                    <span>{String(row.failed_count)} failed</span>
                  </div>
                );
              })}
          </div>
        </>
      );
    if (artifact.type === "ANOMALY_REPORT")
      return (
        <>
          <p className="hypothesis">
            Decision: {payload.decision === "INSUFFICIENT_HISTORY" ? "Not enough history" : String(payload.decision ?? "UNAVAILABLE")} · confidence{" "}
            {typeof payload.confidence === "number"
              ? `${Math.round((payload.confidence as number) * 100)}%`
              : "—"}
          </p>
          {Array.isArray(payload.hypotheses) &&
            (payload.hypotheses as Record<string, unknown>[]).map(
              (item, index) => (
                <p key={index}>
                  {String(item.summary ?? "No hypothesis supplied.")}
                </p>
              ),
            )}
          {payload.error ? (
            <p className="muted">Analysis note: {String(payload.error)}</p>
          ) : null}
        </>
      );
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
            `${artifact.type.replaceAll("_", " ")} generated by ${artifact.agent_role}.`,
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
  const phaseStatus = (phaseIndex: number) => {
    if (phaseIndex < currentPhaseIndex) return "COMPLETED";
    if (phaseIndex > currentPhaseIndex) return "LOCKED";
    return currentStep?.status ?? "READY";
  };
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
      <div className="workflow-layout">
        <aside className="workflow-stepper" aria-label="Four workflow phases">
          {workflowPhases.map((phase, index) => {
            const status = phaseStatus(index);
            return (
              <button
                type="button"
                disabled
                className={`workflow-step ${index === currentPhaseIndex ? "current" : ""} ${status.toLowerCase()}`}
                key={phase.label}
                aria-label={phase.label}
              >
                <div className="workflow-step-index">
                  {status === "COMPLETED" ? "✓" : index + 1}
                </div>
                <div className="workflow-step-copy">
                  <strong>{phase.label}</strong>
                  <span>{phase.owner}</span>
                </div>
              </button>
            );
          })}
        </aside>
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
              pipelineMode={false}
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
  const [showGraph1Studio, setShowGraph1Studio] = useState<boolean>(() => sessionStorage.getItem("ridepulse.graph1.open") === "1" && Boolean(sessionStorage.getItem("ridepulse.graph1.run")));
  const [analysisRunId, setAnalysisRunId] = useState(() => sessionStorage.getItem("ridepulse.analysis.run") ?? "");
  const [showAnalysisStudio, setShowAnalysisStudio] = useState<boolean>(() => sessionStorage.getItem("ridepulse.analysis.open") === "1" && Boolean(sessionStorage.getItem("ridepulse.analysis.run")));
  const [analysisStarting, setAnalysisStarting] = useState(false);
  const [analysisLaunchError, setAnalysisLaunchError] = useState("");
  const [graph1Dataset, setGraph1Dataset] = useState<Dataset | null>(null);
  const [graph1Run, setGraph1Run] = useState<Graph1Run | null>(null);
  const [graph1Nodes, setGraph1Nodes] = useState<Graph1NodeExecution[]>([]);
  const [graph1Starting, setGraph1Starting] = useState(false);
  const [showGraph1Sidebar, setShowGraph1Sidebar] = useState(false);
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
  const [activeJob, setActiveJob] = useState<Job | null>(null);
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
  const [manualRuleOpen, setManualRuleOpen] = useState(false);
  const workspaceRefreshSequence = useRef(0);

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
  const workflowAnalysisComplete = Boolean(
    workflow?.steps.find((step) => step.key === "ANALYZE_REPORT")?.status ===
      "COMPLETED",
  );
  const maxWizardStep = !dataset || !profile
    ? 1
    : workflowAnalysisComplete
      ? 4
      : 3;
  const wizardNextDisabled =
    wizardStep === 4 ||
    (wizardStep === 1 && (!dataset || !profile)) ||
    (wizardStep === 2 && !profile) ||
    (wizardStep === 3 && !workflowAnalysisComplete);

  const refreshWorkspace = useCallback(async () => {
    const refreshId = ++workspaceRefreshSequence.current;
    setLoading(true);
    setError("");
    try {
      const [nextDatasets, nextAudit] = await Promise.all([
        api.listDatasets(),
        api.listAuditLogs(),
      ]);
      if (refreshId !== workspaceRefreshSequence.current) return;
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
      if (refreshId !== workspaceRefreshSequence.current) return;
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
        if (refreshId !== workspaceRefreshSequence.current) return;
        const nextProfile = nextProfiles[nextDataset.id] ?? null;
        setProfile(nextProfile);
        setProposals(nextProposals);
        setRuleConfigurations(nextConfigurations);
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
        const rememberedWorkflowId = sessionStorage.getItem(
          "ridepulse.workflow",
        );
        if (rememberedWorkflowId) {
          try {
            const [rememberedWorkflow, rememberedArtifacts] =
              await Promise.all([
                workflowApi.getWorkflow(rememberedWorkflowId),
                workflowApi.listWorkflowArtifacts(rememberedWorkflowId),
              ]);
            if (
              refreshId === workspaceRefreshSequence.current &&
              rememberedWorkflow.dataset_id === nextDataset.id
            ) {
              setWorkflow(rememberedWorkflow);
              setWorkflowArtifacts(rememberedArtifacts);
              setProposals(
                await api.listProposals(
                  nextDataset.id,
                  rememberedWorkflow.id,
                ),
              );
            } else if (rememberedWorkflow.dataset_id !== nextDataset.id) {
              sessionStorage.removeItem("ridepulse.workflow");
            }
          } catch (err) {
            if (err instanceof ApiError && err.status === 404) {
              sessionStorage.removeItem("ridepulse.workflow");
            } else {
              throw err;
            }
          }
        }
      } else {
        setProfile(null);
        setProposals([]);
        setRuleConfigurations([]);
      }
    } catch (err) {
      if (refreshId !== workspaceRefreshSequence.current) return;
      if (err instanceof ApiError && err.status === 401) {
        clearApiSession();
        sessionStorage.removeItem("ridepulse.auth");
        sessionStorage.removeItem("ridepulse.role");
        sessionStorage.removeItem("ridepulse.username");
        setAuthenticated(false);
      }
      setError(getErrorMessage(err, "Unable to load workspace."));
    } finally {
      if (refreshId === workspaceRefreshSequence.current) setLoading(false);
    }
  }, []);

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
    const changedDataset = datasetId !== selectedDatasetId;
    setShowGraph1Sidebar(false);
    if (changedDataset) {
      setGraph1Run(null);
      setGraph1Nodes([]);
      setGraph1Dataset(null);
      setAnalysisRunId("");
      setAnalysisLaunchError("");
      setShowAnalysisStudio(false);
      sessionStorage.removeItem("ridepulse.graph1.open");
      sessionStorage.removeItem("ridepulse.graph1.run");
      sessionStorage.removeItem("ridepulse.graph1.dataset");
      sessionStorage.removeItem("ridepulse.analysis.open");
      sessionStorage.removeItem("ridepulse.analysis.run");
    }
    sessionStorage.setItem("ridepulse.dataset", datasetId);
    setSelectedDatasetId(datasetId);
    setWorkflow(null);
    setWorkflowArtifacts([]);
    await refreshWorkspace();
  }

  async function openGraph1ForDataset(datasetId: string) {
    if (datasetId !== selectedDatasetId) await selectDataset(datasetId);
    setGraph1Dataset(datasets.find((item) => item.id === datasetId) ?? null);
    setAnalysisLaunchError("");
    setWizardStep(2);
    setShowAdmin(false);
    sessionStorage.setItem("ridepulse.graph1.open", "1");
    setShowGraph1Studio(true);
  }

  async function openAnalysisForGraph1(graph1RunId: string) {
    if (isMockMode) throw new Error("Analysis Studio requires the real backend.");
    setAnalysisStarting(true);
    setAnalysisLaunchError("");
    try {
      const analysisRun = await api.createAnalysisRun(graph1RunId);
      sessionStorage.setItem("ridepulse.analysis.run", analysisRun.id);
      sessionStorage.setItem("ridepulse.analysis.open", "1");
      sessionStorage.removeItem("ridepulse.graph1.open");
      setAnalysisRunId(analysisRun.id);
      setWizardStep(3);
      setShowAnalysisStudio(true);
      setShowGraph1Studio(false);
    } catch (reason) {
      setAnalysisLaunchError(getErrorMessage(reason, "Unable to start Graph 2 and Graph 3."));
      sessionStorage.removeItem("ridepulse.analysis.open");
      sessionStorage.removeItem("ridepulse.analysis.run");
      sessionStorage.removeItem("ridepulse.graph1.open");
      setAnalysisRunId("");
      setWizardStep(3);
      setShowAnalysisStudio(false);
      setShowGraph1Studio(false);
    } finally {
      setAnalysisStarting(false);
    }
  }

  function closeAnalysisStudio() {
    sessionStorage.removeItem("ridepulse.analysis.open");
    sessionStorage.removeItem("ridepulse.graph1.open");
    setShowAnalysisStudio(false);
    setAnalysisLaunchError("");
    // Keep the durable run id so Step 3 can reopen the studio in this session.
    setWizardStep(3);
  }

  function backToGraph1FromAnalysis() {
    sessionStorage.removeItem("ridepulse.analysis.open");
    sessionStorage.setItem("ridepulse.graph1.open", "1");
    setShowAnalysisStudio(false);
    setAnalysisLaunchError("");
    setShowGraph1Studio(true);
    setWizardStep(2);
  }

  async function retryAnalysisFromStep3() {
    const storedRunId = sessionStorage.getItem("ridepulse.graph1.run") ?? "";
    const storedDatasetId = sessionStorage.getItem("ridepulse.graph1.dataset") ?? "";
    const currentDatasetId = dataset?.id ?? selectedDatasetId ?? "";
    const graph1RunId = graph1Run?.dataset_id === currentDatasetId
      ? graph1Run.id
      : storedDatasetId === currentDatasetId
        ? storedRunId
        : "";
    if (!graph1RunId) {
      setAnalysisLaunchError("Complete Graph 1 in Step 2 before starting Graph 2 and Graph 3.");
      return;
    }
    await openAnalysisForGraph1(graph1RunId);
  }

  async function toggleGraph1Sidebar(datasetId: string) {
    if (datasetId !== selectedDatasetId) {
      await selectDataset(datasetId);
      setShowGraph1Sidebar(true);
      return;
    }
    setShowGraph1Sidebar((current) => !current);
  }

  function closeGraph1Sidebar() {
    setShowGraph1Sidebar(false);
    window.requestAnimationFrame(() => document.getElementById("graph1-details-trigger")?.focus());
  }

  const refreshGraph1 = useCallback(async (runId: string) => {
    const [nextRun, nextNodes] = await Promise.all([
      api.getGraph1Run(runId),
      api.listGraph1Nodes(runId),
    ]);
    setGraph1Run(nextRun);
    setGraph1Nodes(nextNodes);
    return nextRun;
  }, []);

  async function startGraph1InBackground(datasetId: string) {
    if (!canOperate || graph1Starting) return;
    setError("");
    setGraph1Starting(true);
    try {
      if (datasetId !== selectedDatasetId) await selectDataset(datasetId);
      const storedRun = sessionStorage.getItem("ridepulse.graph1.run");
      const storedDataset = sessionStorage.getItem("ridepulse.graph1.dataset");
      const nextRun = storedRun && storedDataset === datasetId
        ? await api.getGraph1Run(storedRun)
        : await api.createGraph1Run(datasetId);
      sessionStorage.setItem("ridepulse.graph1.run", nextRun.id);
      sessionStorage.setItem("ridepulse.graph1.dataset", datasetId);
      setGraph1Run(nextRun);
      setGraph1Nodes(await api.listGraph1Nodes(nextRun.id));
      setToast("Agent Workflow is running in the background.");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to start Agent Workflow."));
    } finally {
      setGraph1Starting(false);
    }
  }

  useEffect(() => {
    if (!dataset) return;
    const storedRun = sessionStorage.getItem("ridepulse.graph1.run");
    const storedDataset = sessionStorage.getItem("ridepulse.graph1.dataset");
    if (!storedRun || storedDataset !== dataset.id) {
      setGraph1Run(null);
      setGraph1Nodes([]);
      return;
    }
    void refreshGraph1(storedRun).catch(() => {
      sessionStorage.removeItem("ridepulse.graph1.run");
      sessionStorage.removeItem("ridepulse.graph1.dataset");
      setGraph1Run(null);
      setGraph1Nodes([]);
    });
  }, [dataset?.id, refreshGraph1]);

  useEffect(() => {
    if (!graph1Run || ["COMPLETED", "FAILED", "AWAITING_SEMANTIC_REVIEW", "AWAITING_RULE_REVIEW"].includes(graph1Run.status)) return;
    const timer = window.setInterval(() => void refreshGraph1(graph1Run.id), 2500);
    return () => window.clearInterval(timer);
  }, [graph1Run?.id, graph1Run?.status, refreshGraph1]);

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
      setLoginError(
        err instanceof ApiError && err.status === 401
          ? language === "vi"
            ? "Tên đăng nhập hoặc mật khẩu không đúng."
            : "The username or password is incorrect."
          : getErrorMessage(err, "Unable to start session."),
      );
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
    sessionStorage.removeItem("ridepulse.analysis.open");
    sessionStorage.removeItem("ridepulse.analysis.run");
    setShowAnalysisStudio(false);
    setAnalysisRunId("");
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
      attempt < 120 &&
      !["SUCCEEDED", "FAILED", "FAILED_RETRYABLE"].includes(current.status);
      attempt += 1
    ) {
      await sleep(450);
      current = await jobApi.getJob(acceptedJob.job_id);
      setActiveJob(current);
    }
    const finalStatus = current.status as Job["status"];
    if (finalStatus === "SUCCEEDED") {
      await onComplete();
      setActiveJob(null);
      setRetryAction(null);
      setToast(
        language === "vi"
          ? "Tác vụ đã hoàn thành thành công."
          : "Job completed successfully.",
      );
    } else {
      setRetryAction(() => () => void pollJob(acceptedJob, onComplete, jobApi));
      setError(
        current.error ??
        "The job did not complete. Retry the operation when ready.",
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
      setError(getErrorMessage(err, "Unable to start analysis."));
    }
  }

  useEffect(() => {
    if (wizardStep === 2 && dataset && !profile && !activeJob) {
      void startAnalysis();
    }
  }, [wizardStep, dataset, profile, activeJob]); // eslint-disable-line react-hooks/exhaustive-deps

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
      await pollJob(imported.job, async () => {
        await refreshWorkspace();
        setGraph1Dataset({ ...imported.dataset, status: "PROFILE_READY" });
        setWizardStep(1);
        sessionStorage.removeItem("ridepulse.graph1.open");
        setShowGraph1Studio(false);
      });
    } catch (err) {
      setError(getErrorMessage(err, "Unable to import dataset."));
    }
  }

  async function deleteDataset(id: string) {
    try {
      await api.deleteDataset(id);
      setDatasets((current) => current.filter((d) => d.id !== id));
      if (selectedDatasetId === id) {
        const remaining = datasets.filter((d) => d.id !== id);
        const nextId = remaining[0]?.id ?? "";
        setSelectedDatasetId(nextId);
        if (nextId) sessionStorage.setItem("ridepulse.dataset", nextId);
        else sessionStorage.removeItem("ridepulse.dataset");
      }
      setToast(t("datasets.removedToast"));
    } catch (err) {
      setError(getErrorMessage(err, "Unable to delete dataset."));
    }
  }

  async function requestProposals() {
    if (!dataset) return;
    setError("");
    setRetryAction(null);
    try {
      const job = await api.startRuleProposals(dataset.id, crypto.randomUUID());
      await pollJob(job, async () => {
        setProposals(await api.listProposals(dataset.id, workflow?.id));
        setAuditLogs(await api.listAuditLogs());
        setView("rules");
      });
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
          ? (language === "vi" ? "Đã chấp nhận quy tắc thực thi." : "Rule approved for execution.")
          : (language === "vi" ? "Đã từ chối đề xuất quy tắc." : "Proposal rejected and kept out of execution."),
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
      setToast(language === "vi" ? "Đã xóa đề xuất quy tắc." : "Proposal removed. Audit history was retained.");
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
      setToast(language === "vi" ? "Đã lưu thiết lập cấu hình thực thi." : "Execution settings saved.");
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
      setToast(language === "vi" ? "Đã chỉnh sửa quy tắc thành công." : "Proposal edited and marked ready for approval.");
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
      setToast(language === "vi" ? "Đã tạo quy tắc thủ công mới." : "Manual rule created and queued for approval.");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to create manual rule."));
    }
  }

  async function runApprovedRules() {
    try {
      const queuedRun = await api.startDqRun(
        approvedRules.map((rule) => rule.id),
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

  async function startWorkflowStep(
    step: WorkflowStepKey,
    fresh = false,
    requestedDatasetId?: string,
  ) {
    const targetDatasetId = requestedDatasetId ?? dataset?.id;
    if (!targetDatasetId || !canOperate || workflowActionBusy || activeJob)
      return;
    setError("");
    setRetryAction(null);
    setWorkflowActionBusy(true);
    try {
      if (!workflow && step === "UPLOAD_PROFILE") {
        const ingestion = await api.startIngestion(
          targetDatasetId,
          crypto.randomUUID(),
        );
        await pollJob(ingestion, async () => {
          const [nextDatasets, currentWorkflow] = await Promise.all([
            api.listDatasets(),
            workflowApi.createWorkflow(targetDatasetId, true),
          ]);
          setDatasets(nextDatasets);
          setProfile(await api.getProfile(targetDatasetId));
          setWorkflow(currentWorkflow);
          sessionStorage.setItem("ridepulse.workflow", currentWorkflow.id);
          setWorkflowArtifacts(
            await workflowApi.listWorkflowArtifacts(currentWorkflow.id),
          );
          setAuditLogs(await api.listAuditLogs());
        });
        return;
      }
      let currentWorkflow =
        workflow?.dataset_id === targetDatasetId ? workflow : null;
      if (!currentWorkflow || fresh) {
        currentWorkflow = await workflowApi.createWorkflow(
          targetDatasetId,
          fresh,
        );
        setWorkflow(currentWorkflow);
        sessionStorage.setItem("ridepulse.workflow", currentWorkflow.id);
        setWorkflowArtifacts(
          await workflowApi.listWorkflowArtifacts(currentWorkflow.id),
        );
        setProposals(
          await api.listProposals(targetDatasetId, currentWorkflow.id),
        );
      }
      const queuedJob = await workflowApi.runWorkflowStep(
        currentWorkflow.id,
        step,
      );
      await pollJob(
        queuedJob,
        async () => {
          await refreshWorkflow(currentWorkflow!.id);
          setProfile(await api.getProfile(targetDatasetId));
          setProposals(
            await api.listProposals(targetDatasetId, currentWorkflow!.id),
          );
          setRuleConfigurations(
            await api.listRuleConfigurations(targetDatasetId),
          );
          if (step === "RUN_CHECKS" || step === "ANALYZE_REPORT") {
            const latestRun = await api.getLatestDqRun(targetDatasetId);
            setActiveRun(latestRun);
            if (latestRun?.status === "SUCCEEDED") {
              const [latestResults, latestAnomalies, nextTrends] =
                await Promise.all([
                  api.getDqResults(latestRun.id),
                  api.getDqAnomalies(latestRun.id),
                  api.getQualityTrends(targetDatasetId),
                ]);
              setDqResults(latestResults);
              setDqAnomalies(latestAnomalies);
              setQualityTrends(nextTrends);
            }
          }
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

  async function startDatasetUnderstanding(datasetId: string) {
    if (!canOperate || workflowActionBusy || activeJob) return;
    // Prevent a slower initial workspace refresh from overwriting the new
    // workflow-scoped profile, proposals, and artifacts with dataset history.
    workspaceRefreshSequence.current += 1;
    setLoading(false);
    sessionStorage.setItem("ridepulse.dataset", datasetId);
    setSelectedDatasetId(datasetId);
    setError("");
    setRetryAction(null);

    try {
      let nextProfile: DatasetProfile | null = null;
      try {
        nextProfile = await api.getProfile(datasetId);
      } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 404) throw err;
      }

      if (!nextProfile) {
        const ingestion = await api.startIngestion(
          datasetId,
          crypto.randomUUID(),
        );
        await pollJob(ingestion, async () => {
          nextProfile = await api.getProfile(datasetId);
          setDatasets(await api.listDatasets());
        });
      }

      if (nextProfile) {
        setProfile(nextProfile);
        setDatasetProfiles((current) => ({
          ...current,
          [datasetId]: nextProfile!,
        }));
      }
      await startWorkflowStep("UNDERSTAND_DATA", true, datasetId);
    } catch (err) {
      setError(
        getErrorMessage(
          err,
          "Unable to prepare the profile and run the Understand Data Agent.",
        ),
      );
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
            {canAdmin && (
              <button
                type="button"
                className={`button secondary ${showAdmin ? "active" : ""}`}
                onClick={() => {
                  setShowAdmin(!showAdmin);
                  sessionStorage.removeItem("ridepulse.graph1.open");
                  setShowGraph1Studio(false);
                  sessionStorage.removeItem("ridepulse.analysis.open");
                  setShowAnalysisStudio(false);
                }}
              >
                ⚙ {t("app.adminControl")}
              </button>
            )}
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

        {!showAdmin && !showGraph1Studio && !showAnalysisStudio && (
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
                    disabled={
                      step.id > maxWizardStep ||
                      Boolean(activeJob) ||
                      workflowActionBusy
                    }
                    className={`wizard-step-node ${wizardStep === step.id
                      ? "active"
                      : wizardStep > step.id
                        ? "completed"
                        : ""
                      }`}
                    onClick={() => {
                      if (step.id > maxWizardStep) return;
                      setShowAdmin(false);
                      setAnalysisLaunchError("");
                      setShowGraph1Sidebar(false);
                      sessionStorage.removeItem("ridepulse.graph1.open");
                      setShowGraph1Studio(false);
                      const persistedAnalysisRunId = sessionStorage.getItem("ridepulse.analysis.run") ?? "";
                      if (step.id === 3 && persistedAnalysisRunId) {
                        setAnalysisRunId(persistedAnalysisRunId);
                        sessionStorage.setItem("ridepulse.analysis.open", "1");
                        setShowAnalysisStudio(true);
                      } else {
                        if (step.id === 3) setAnalysisRunId("");
                        sessionStorage.removeItem("ridepulse.analysis.open");
                        setShowAnalysisStudio(false);
                      }
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
          {!showGraph1Studio && !showAnalysisStudio && !canOperate && (
            <div className="dev-banner">
              <span>Read-only access</span>
              <span>
                Your role can inspect evidence and results but cannot change
                rules or start jobs.
              </span>
              <code>{role}</code>
            </div>
          )}
          {!showGraph1Studio && !showAnalysisStudio && isMockMode && (
            <div className="dev-banner">
              <span>Local development adapter</span>
              <span>
                Results are deterministic fixtures until the Gate 2 backend is
                connected.
              </span>
              <code>VITE_USE_MOCK_API=false</code>
            </div>
          )}
          {!showGraph1Studio && !showAnalysisStudio && error && (
            <div className="alert error">
              <strong>Action failed</strong>
              <span>{error}</span>
              <button onClick={() => setError("")}>×</button>
            </div>
          )}
          {(toast || activeJob) && (
            <div className="floating-toasts-stack">
              {activeJob && (
                <ProgressPanel
                  job={activeJob}
                  title={
                    activeJob.type === "INGEST_PROFILE"
                      ? (language === "vi" ? "Đang phân tích hồ sơ dữ liệu…" : "Building dataset profile")
                      : activeJob.type === "PROPOSE_RULES"
                        ? (language === "vi" ? "Đang sinh đề xuất quy tắc…" : "Generating rule proposals")
                        : activeJob.type === "RUN_DQ" &&
                            /ANALYZE_REPORT|analysis report/i.test(activeJob.message)
                          ? (language === "vi" ? "Đang phân tích và tạo báo cáo…" : "Analyzing results and building report")
                        : (language === "vi" ? "Đang chạy kiểm thử quy tắc…" : "Running approved checks")
                  }
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

          {showAnalysisStudio && analysisRunId ? (
            <AnalysisStudio
              analysisRunId={analysisRunId}
              onExit={closeAnalysisStudio}
              onBackToGraph1={backToGraph1FromAnalysis}
            />
          ) : showGraph1Studio ? (
            <Graph1Studio
              onExit={() => {
                sessionStorage.removeItem("ridepulse.graph1.open");
                setShowGraph1Studio(false);
                setGraph1Dataset(null);
                setWizardStep(1);
                const runId = sessionStorage.getItem("ridepulse.graph1.run");
                if (runId) void refreshGraph1(runId);
              }}
              onDatasetImported={() => void refreshWorkspace()}
              onAnalyze={openAnalysisForGraph1}
              initialDataset={graph1Dataset ?? dataset}
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
              {/* STEP 1: Dataset Preparation */}
              {wizardStep === 1 && (
                <div>
                  <DatasetsPage
                    datasets={datasets}
                    dataset={dataset}
                    onOpenExplorer={(datasetId) => {
                      if (datasetId !== dataset?.id) void selectDataset(datasetId);
                      setShowDataExplorer(true);
                    }}
                    onImportDataset={(file) => void importDataset(file)}
                    onSelectDataset={(id) => void selectDataset(id)}
                    onDeleteDataset={(id) => void deleteDataset(id)}
                    onStartUnderstand={(id) => void openGraph1ForDataset(id)}
                    onViewNodeDetails={(id) => { void toggleGraph1Sidebar(id); }}
                    canOperate={canOperate}
                    importing={Boolean(activeJob)}
                    busy={workflowActionBusy || graph1Starting}
                    graph1Run={graph1Run}
                    graph1Nodes={graph1Nodes}
                    showNodeDetails={showGraph1Sidebar}
                    onRefreshGraph1={refreshGraph1}
                  />
                </div>
              )}

              {/* STEP 2: Graph 1 execution studio */}
              {wizardStep === 2 && (
                <div>
                  <Graph1Studio
                    onExit={() => setWizardStep(1)}
                    onDatasetImported={() => void refreshWorkspace()}
                    onAnalyze={openAnalysisForGraph1}
                    initialDataset={dataset}
                  />
                </div>
              )}
              {/* Legacy quality profiling UI retained below for reference */}
              {false && wizardStep === 2 && (
                <div>
                  <div className="page-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <span className="eyebrow">STEP 2 · {t("wizard.step2Title").toUpperCase()}</span>
                      <h1>{dataset ? dataset.name : t("overview.title")}</h1>
                      <p>{t("wizard.step2Desc")}</p>
                    </div>
                    {dataset && (
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "4px" }}>
                        {profile ? (
                          <>
                            <div style={{ width: "48px", height: "48px", borderRadius: "50%", border: `3px solid ${profile!.validity_score >= 90 ? "#10b981" : profile!.validity_score >= 75 ? "#f59e0b" : "#ef4444"}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: "20px", fontWeight: "800", color: profile!.validity_score >= 90 ? "#10b981" : profile!.validity_score >= 75 ? "#f59e0b" : "#ef4444", background: "var(--surface)", boxShadow: "0 2px 10px rgba(0,0,0,0.05)" }}>
                              {profile!.validity_score >= 90 ? "A" : profile!.validity_score >= 75 ? "B" : profile!.validity_score >= 60 ? "C" : "D"}
                            </div>
                            <span style={{ fontSize: "10px", fontWeight: 700, color: "var(--muted)", letterSpacing: "0.5px" }}>GRADE</span>
                          </>
                        ) : activeJob ? (
                          <div className="workflow-pending-indicator" style={{ width: "24px", height: "24px", margin: "12px" }} />
                        ) : null}
                      </div>
                    )}
                  </div>

                  {!dataset ? (
                    <div className="alert warning">{t("workflow.noDatasetSelected")}</div>
                  ) : (
                    <div>
                      {/* Dataset Header Badges */}
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "12px", marginTop: "16px", marginBottom: "32px", alignItems: "center" }}>
                        <span className="status-pill" style={{ fontSize: "13px", padding: "6px 12px", background: "var(--surface)", border: "1px solid var(--border)", color: "var(--muted)", display: "flex", alignItems: "center", gap: "6px" }}>
                          <strong style={{ color: "var(--ink)", fontWeight: 600 }}>{t("datasets.rows")}:</strong>
                          {(profile?.row_count ?? dataset.row_count).toLocaleString()}
                        </span>
                        <span className="status-pill" style={{ fontSize: "13px", padding: "6px 12px", background: "var(--surface)", border: "1px solid var(--border)", color: "var(--muted)", display: "flex", alignItems: "center", gap: "6px" }}>
                          <strong style={{ color: "var(--ink)", fontWeight: 600 }}>{t("datasets.columns")}:</strong>
                          {profile?.columns.length ?? 0}
                        </span>
                        <span className="status-pill" style={{ fontSize: "13px", padding: "6px 12px", background: "var(--surface)", border: "1px solid var(--border)", color: "var(--muted)", display: "flex", alignItems: "center", gap: "6px" }}>
                          <strong style={{ color: "var(--ink)", fontWeight: 600 }}>{t("datasets.source")}:</strong>
                          {dataset.source_label}
                        </span>
                      </div>

                      {/* Quality Metrics */}
                      <div className="section-header" style={{ marginBottom: "20px" }}>
                        <h3 style={{ fontSize: "18px", fontWeight: "700", color: "var(--ink)" }}>{t("datasets.qualityMetrics")}</h3>
                      </div>

                      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "16px", marginBottom: "32px" }}>
                        {/* Completeness Card */}
                        <div style={{ padding: "24px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "16px", boxShadow: "0 2px 12px rgba(0,0,0,0.03)" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: "16px" }}>
                            <span style={{ fontSize: "13px", fontWeight: "700", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>{t("overview.completeness")}</span>
                            <span style={{ fontSize: "28px", fontWeight: "800", color: "var(--accent)", lineHeight: "1" }}>{profile ? `${profile!.completeness_score.toFixed(1)}%` : "—"}</span>
                          </div>
                          {profile && (
                            <div style={{ width: "100%", height: "8px", background: "var(--surface-muted)", borderRadius: "999px", overflow: "hidden", marginBottom: "16px" }}>
                              <div style={{ width: `${profile!.completeness_score}%`, height: "100%", background: "var(--accent)", borderRadius: "999px", transition: "width 1s cubic-bezier(0.4, 0, 0.2, 1)" }} />
                            </div>
                          )}
                          <div style={{ fontSize: "13px", color: "var(--muted)", lineHeight: "1.4" }}>{t("datasets.nonNullRatio")}</div>
                        </div>

                        {/* Duplicate Rate Card */}
                        <div style={{ padding: "24px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "16px", boxShadow: "0 2px 12px rgba(0,0,0,0.03)" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: "16px" }}>
                            <span style={{ fontSize: "13px", fontWeight: "700", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>{t("overview.duplicateRate")}</span>
                            <span style={{ fontSize: "28px", fontWeight: "800", color: profile && profile!.duplicate_rate > 5 ? "#d97706" : "#059669", lineHeight: "1" }}>{profile ? `${profile!.duplicate_rate.toFixed(2)}%` : "—"}</span>
                          </div>
                          {profile && (
                            <div style={{ width: "100%", height: "8px", background: "var(--surface-muted)", borderRadius: "999px", overflow: "hidden", marginBottom: "16px" }}>
                              <div style={{ width: `${Math.min(profile!.duplicate_rate, 100)}%`, height: "100%", background: profile!.duplicate_rate > 5 ? "#d97706" : "#059669", borderRadius: "999px", transition: "width 1s cubic-bezier(0.4, 0, 0.2, 1)" }} />
                            </div>
                          )}
                          <div style={{ fontSize: "13px", color: "var(--muted)", lineHeight: "1.4" }}>{t("datasets.duplicateRowsRatio")}</div>
                        </div>

                        {/* Validity Score Card */}
                        <div style={{ padding: "24px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "16px", boxShadow: "0 2px 12px rgba(0,0,0,0.03)" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: "16px" }}>
                            <span style={{ fontSize: "13px", fontWeight: "700", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.5px" }}>{t("datasets.validityScore")}</span>
                            <span style={{ fontSize: "28px", fontWeight: "800", color: "#2563eb", lineHeight: "1" }}>{profile ? `${profile!.validity_score.toFixed(1)}%` : "—"}</span>
                          </div>
                          {profile && (
                            <div style={{ width: "100%", height: "8px", background: "var(--surface-muted)", borderRadius: "999px", overflow: "hidden", marginBottom: "16px" }}>
                              <div style={{ width: `${profile!.validity_score}%`, height: "100%", background: "#2563eb", borderRadius: "999px", transition: "width 1s cubic-bezier(0.4, 0, 0.2, 1)" }} />
                            </div>
                          )}
                          <div style={{ fontSize: "13px", color: "var(--muted)", lineHeight: "1.4" }}>{t("datasets.schemaDomainValidity")}</div>
                        </div>
                      </section>

                      {/* Schema & Column Breakdown Table for selected dataset */}
                      {profile?.columns && profile!.columns.length > 0 ? (
                        <section className="panel" style={{ marginTop: "24px", padding: "24px" }}>
                          <div className="panel-heading" style={{ marginBottom: "16px" }}>
                            <div>
                              <span className="eyebrow">{t("datasets.schemaBreakdown")}</span>
                              <h3>{t("datasets.columnHealth").replace("{{count}}", profile!.columns.length.toString())}</h3>
                            </div>
                          </div>
                          <div style={{ overflowX: "auto" }}>
                            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "14px" }}>
                              <thead>
                                <tr style={{ borderBottom: "2px solid var(--border, #cbd5e1)", textAlign: "left", whiteSpace: "nowrap" }}>
                                  <th style={{ padding: "10px" }}>{t("datasets.colName")}</th>
                                  <th style={{ padding: "10px" }}>{t("datasets.colDataType")}</th>
                                  <th style={{ padding: "10px" }}>{t("datasets.colNullRate")}</th>
                                  <th style={{ padding: "10px" }}>{t("datasets.colUniqueness")}</th>
                                  <th style={{ padding: "10px" }}>{t("datasets.colSampleValue")}</th>
                                </tr>
                              </thead>
                              <tbody>
                                {profile!.columns.map((col, idx) => (
                                  <tr key={idx} style={{ borderBottom: "1px solid var(--border, #f1f5f9)" }}>
                                    <td style={{ padding: "10px", fontWeight: 600 }}><code>{col.name}</code></td>
                                    <td style={{ padding: "10px" }}><span className="status-pill info">{col.data_type}</span></td>
                                    <td style={{ padding: "10px" }}>{(col.null_rate * 100).toFixed(1)}%</td>
                                    <td style={{ padding: "10px" }}>{col.distinct_count !== undefined ? t("datasets.distinctValues").replace("{{count}}", col.distinct_count.toLocaleString()) : "—"}</td>
                                    <td style={{ padding: "10px", color: "var(--muted)", fontSize: "12px" }}><code>{col.sample_value || "—"}</code></td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </section>
                      ) : (
                        <div style={{ textAlign: "center", padding: "40px", background: "var(--surface-muted, #f8fafc)", borderRadius: "12px", marginTop: "24px", border: "1px dashed var(--border, #e2e8f0)" }}>
                          {activeJob && (
                            <div className="workflow-pending-indicator" style={{ margin: "0 auto 16px auto", width: "20px", height: "20px" }} />
                          )}
                          <p className="muted" style={{ margin: 0, fontSize: "14px" }}>
                            {activeJob
                              ? t("datasets.profiling")
                              : language === "vi"
                                ? "Chưa có profile hoàn chỉnh. Hệ thống sẽ tạo profile trước khi mở bước tiếp theo."
                                : "No complete profile is available. Build the profile before continuing."}
                          </p>
                        </div>
                      )}

                      {/* LLM Data Analyst Summary */}
                      {profile && (
                        <div style={{ marginTop: "32px", padding: "24px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "16px", boxShadow: "0 2px 12px rgba(0,0,0,0.03)" }}>
                          <div style={{ display: "flex", gap: "12px", alignItems: "center", marginBottom: "16px" }}>
                            <h3 style={{ fontSize: "16px", fontWeight: "700", color: "var(--ink)", margin: 0 }}>{t("datasets.aiSummaryTitle")}</h3>
                          </div>
                          <div style={{ fontSize: "14px", lineHeight: "1.6", color: "var(--ink-soft)" }}>
                            <p style={{ margin: 0 }}>
                              Dựa trên kết quả phân tích hệ thống, tập dữ liệu <strong>{dataset.name}</strong> đạt mức độ hoàn thiện <strong>{profile!.completeness_score.toFixed(1)}%</strong> và độ tin cậy cấu trúc <strong>{profile!.validity_score.toFixed(1)}%</strong>.
                              {profile!.duplicate_rate > 5
                                ? ` Tuy nhiên, tỷ lệ trùng lặp đang ở ngưỡng cảnh báo (${profile!.duplicate_rate.toFixed(1)}%), có thể gây ảnh hưởng đến tính toàn vẹn của các phân tích chuyên sâu.`
                                : ` Tỷ lệ trùng lặp được duy trì ở mức an toàn (${profile!.duplicate_rate.toFixed(1)}%), đảm bảo dữ liệu sạch và không bị nhiễu.`
                              }
                              {profile!.validity_score < 80
                                ? ` Khuyến nghị thiết lập thêm các quy tắc kiểm duyệt (Guardrails) nghiêm ngặt ở bước tiếp theo để ngăn chặn dữ liệu rác thâm nhập vào các luồng xử lý downstream.`
                                : ` Chất lượng dữ liệu nhìn chung đạt chuẩn (Grade ${profile!.validity_score >= 90 ? "A" : profile!.validity_score >= 75 ? "B" : "C"}) và hoàn toàn đủ điều kiện sử dụng làm nguồn tham chiếu tin cậy.`
                              }
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* STEP 3: Execution & Rules Selection & Monitoring */}
              {wizardStep === 3 && (
                <div>
                  {analysisRunId && showAnalysisStudio ? (
                    <AnalysisStudio
                      analysisRunId={analysisRunId}
                      onExit={closeAnalysisStudio}
                      onBackToGraph1={backToGraph1FromAnalysis}
                    />
                  ) : (
                    <section className="panel" style={{ marginTop: "16px", padding: "28px", textAlign: "center" }}>
                      <span className="eyebrow">STEP 3 · GRAPH 2 + GRAPH 3</span>
                      <h2 style={{ margin: "8px 0" }}>Agent execution studio</h2>
                      <p
                        className="muted"
                        role={analysisLaunchError ? "alert" : undefined}
                        style={{ maxWidth: "560px", margin: "0 auto 18px" }}
                      >
                        {analysisLaunchError || (analysisRunId
                          ? "The Agent execution studio is ready to resume this analysis run."
                          : "Complete Graph 1 in Step 2, then start the Graph 2 and Graph 3 analysis.")}
                      </p>
                      <div style={{ display: "flex", justifyContent: "center", gap: "10px", flexWrap: "wrap" }}>
                        {analysisRunId ? (
                          <button
                            type="button"
                            className="button primary"
                            onClick={() => {
                              sessionStorage.setItem("ridepulse.analysis.open", "1");
                              setShowAnalysisStudio(true);
                            }}
                          >
                            Open Agent execution studio →
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="button primary"
                            disabled={analysisStarting}
                            onClick={() => void retryAnalysisFromStep3()}
                          >
                            {analysisStarting ? "Starting analysis…" : "Analyze Graph 2 & 3 →"}
                          </button>
                        )}
                        <button type="button" className="button secondary" onClick={() => { setWizardStep(2); setShowAnalysisStudio(false); }}>
                          Back to Graph 1
                        </button>
                      </div>
                    </section>
                  )}
                </div>
              )}
              {false && wizardStep === 3 && (
                <div>
                  <RulesPage
                    proposals={proposals}
                    configurations={ruleConfigurations}
                    profileReady={Boolean(profile || dataset)}
                    busy={Boolean(activeJob)}
                    canOperate={canOperate}
                    onRequestProposals={() => void requestProposals()}
                    onApprove={(id) => void reviewProposal(id, "approve")}
                    onReject={(id) => void reviewProposal(id, "reject")}
                    onEdit={setEditingProposal}
                    onDelete={(id) => void deleteProposal(id)}
                    onSaveConfiguration={(id, input) =>
                      void saveRuleConfiguration(id, input)
                    }
                    onCreateManual={() => setManualRuleOpen(true)}
                    onRun={() => void runApprovedRules()}
                    pipelineMode={false}
                  />

                  {/* Execution Section: Show action banner if approved rules exist but no active run yet */}
                  {!activeRun && approvedRules.length > 0 && (
                    <div style={{
                      marginTop: "24px",
                      padding: "20px 24px",
                      background: "linear-gradient(135deg, rgba(37, 99, 235, 0.04) 0%, rgba(16, 185, 129, 0.04) 100%)",
                      border: "1px solid var(--border)",
                      borderRadius: "16px",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center"
                    }}>
                      <div>
                        <span className="status-pill success" style={{ fontSize: "11px", marginBottom: "6px", display: "inline-flex" }}>
                          ✓ {approvedRules.length} quy tắc đã sẵn sàng
                        </span>
                        <h3 style={{ fontSize: "16px", fontWeight: "700", color: "var(--ink)", margin: "4px 0" }}>
                          {t("runs.title")}
                        </h3>
                        <p style={{ fontSize: "13px", color: "var(--muted)", margin: 0 }}>
                          {t("runs.noRunDesc")}
                        </p>
                      </div>
                      <button
                        className="button primary"
                        style={{ fontSize: "14px", padding: "10px 20px" }}
                        onClick={() => void runApprovedRules()}
                        disabled={Boolean(activeJob) || !canOperate}
                      >
                        ⚡ {t("runs.runApproved")}
                      </button>
                    </div>
                  )}

                  {/* Show full execution monitoring page if a run exists */}
                  {activeRun && (
                    <div style={{ marginTop: "32px", borderTop: "1px solid var(--border)", paddingTop: "24px" }}>
                      <RunsPage
                        activeRun={activeRun}
                        results={dqResults}
                        anomalies={dqAnomalies}
                        approvedCount={approvedRules.length}
                        busy={Boolean(activeJob)}
                        canOperate={canOperate}
                        onRun={() => void runApprovedRules()}
                      />
                    </div>
                  )}

                  {/* Audit History Log */}
                  {auditLogs.length > 0 && (
                    <div style={{ marginTop: "40px", borderTop: "1px solid var(--border)", paddingTop: "24px" }}>
                      <AuditPage logs={auditLogs} />
                    </div>
                  )}
                </div>
              )}

              {/* STEP 4: Analytics Dashboard */}
              {wizardStep === 4 && (
                <Step5Analytics
                  results={dqResults}
                  anomalies={dqAnomalies}
                  trends={qualityTrends}
                  onBack={() => setWizardStep(3)}
                  onStartNewRun={() => setWizardStep(1)}
                />
              )}

              {/* Wizard Bottom Nav Controls */}
              <div className="wizard-footer-nav">
                <button
                  type="button"
                  className="button secondary"
                  disabled={wizardStep === 1}
                  title={wizardStep === 1 ? (t("wizard.firstStepTooltip") || "Đây là bước đầu tiên") : ""}
                  onClick={() => setWizardStep((prev) => Math.max(1, prev - 1))}
                >
                  {t("wizard.back")}
                </button>

                <span className="muted" style={{ fontWeight: 600 }}>
                  {t("wizard.stepProgress", { current: wizardStep, total: 4 })}
                </span>

                <button
                  type="button"
                  className="button primary"
                  disabled={wizardNextDisabled}
                  title={
                    !dataset && wizardStep === 1
                      ? (t("wizard.selectDatasetTooltip") || "Vui lòng chọn hoặc tải lên một bộ dữ liệu ở Bước 1")
                      : wizardStep === 1 && !profile
                        ? (language === "vi" ? "Hãy tạo profile cho tập dữ liệu trước" : "Build the dataset profile first")
                      : wizardStep === 4
                        ? (t("wizard.lastStepTooltip") || "Bạn đang ở bước cuối cùng")
                        : ""
                  }
                  onClick={() => {
                    if (wizardStep === 1 && dataset) {
                      void openGraph1ForDataset(dataset.id);
                      return;
                    }
                    setWizardStep((prev) => Math.min(4, prev + 1));
                  }}
                >
                  {wizardStep === 1 && dataset && profile ? "Generate Rules →" : t("wizard.next")}
                </button>
              </div>
            </>
          )}
          {showGraph1Sidebar && wizardStep === 1 && !showAdmin && !showGraph1Studio && (
            <Graph1DetailsSidebar
              run={graph1Run}
              nodes={graph1Nodes}
              dataset={dataset}
              onClose={closeGraph1Sidebar}
              canOperate={canOperate}
              onRefresh={refreshGraph1}
            />
          )}
        </div>
      </main>
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
  const profiledRows = qualityRows.filter((row) => row.score !== null);
  const averageQuality = profiledRows.length
    ? profiledRows.reduce((sum, row) => sum + (row.score ?? 0), 0) /
    profiledRows.length
    : null;
  const averageCompleteness = profiledRows.length
    ? profiledRows.reduce((sum, row) => sum + (row.profile?.completeness_score ?? 0), 0) / profiledRows.length
    : null;
  const averageDuplicateRate = profiledRows.length
    ? profiledRows.reduce((sum, row) => sum + (row.profile?.duplicate_rate ?? 0), 0) / profiledRows.length
    : null;
  const attentionCount = qualityRows.filter((row) => row.score !== null && row.score < 85).length;
  const totalRows = datasets.reduce((sum, item) => sum + item.row_count, 0);
  const profileReadyCount = datasets.filter(
    (item) => item.status === "PROFILE_READY",
  ).length;
  const { t } = useI18n();
  const statusRows = [
    {
      label: t("overview.profileReady"),
      count: datasets.filter((item) => item.status === "PROFILE_READY").length,
    },
    {
      label: t("overview.ingested"),
      count: datasets.filter((item) => item.status === "INGESTED").length,
    },
    {
      label: t("overview.registered"),
      count: datasets.filter((item) => item.status === "REGISTERED").length,
    },
    { label: t("overview.needsAttention"), count: attentionCount },
  ];
  const statusMax = Math.max(1, ...statusRows.map((row) => row.count));
  if (!dataset)
    return (
      <>
        <div className="page-heading">
          <div>
            <span className="eyebrow">{t("overview.eyebrow")}</span>
            <h1>{t("overview.noDatasetTitle")}</h1>
            <p>{t("overview.noDatasetDesc")}</p>
          </div>
        </div>
        <section className="empty-state">
          <div className="empty-illustration">▦</div>
          <h2>{t("overview.catalogEmpty")}</h2>
          <p>
            {t("overview.catalogEmptyDesc")}
          </p>
        </section>
      </>
    );
  return (
    <>
      <div className="page-heading overview-heading">
        <div>
          <span className="eyebrow">{t("overview.eyebrow")}</span>
          <h1>{t("overview.title")}</h1>
          <p>
            {t("overview.subtitle")}
          </p>
        </div>
        <div className="heading-actions">
          <button
            className="button ghost"
            onClick={() => onNavigate("datasets")}
          >
            {t("overview.datasetCatalog")}
          </button>
          <button
            className="button primary"
            onClick={() => onNavigate("visualization")}
          >
            {t("overview.openObservatory")}
          </button>
        </div>
      </div>
      <section className="stat-grid overview-kpis">
        <StatCard
          label="Datasets"
          value={`${datasets.length}`}
          detail="Registered in workspace"
          tone="green"
        />
        <StatCard
          label="Profile ready"
          value={`${profileReadyCount}/${datasets.length}`}
          detail="Datasets with aggregate profile"
          tone="blue"
        />
        <StatCard
          label="Rows tracked"
          value={totalRows.toLocaleString()}
          detail="Across registered datasets"
          tone="amber"
        />
        <StatCard
          label="Average quality"
          value={
            averageQuality === null ? "—" : `${averageQuality.toFixed(1)}%`
          }
          detail={
            profiledRows.length
              ? `${profiledRows.length} profiled dataset${profiledRows.length === 1 ? "" : "s"}`
              : "Awaiting profile data"
          }
          tone="violet"
        />
        <StatCard
          label="Completeness"
          value={averageCompleteness === null ? "—" : `${averageCompleteness.toFixed(1)}%`}
          detail="Average across profiles"
          tone="blue"
        />
        <StatCard
          label="Duplicate rate"
          value={averageDuplicateRate === null ? "—" : `${averageDuplicateRate.toFixed(2)}%`}
          detail={attentionCount ? `${attentionCount} dataset${attentionCount === 1 ? "" : "s"} need attention` : "No quality alerts"}
          tone={attentionCount ? "amber" : "green"}
        />
      </section>
      <section className="overview-grid">
        <article className="panel overview-dataset-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">CATALOG QUALITY MAP</span>
              <h3>Quality by dataset</h3>
            </div>
            <span className="panel-caption">{datasets.length} registered · Click to select</span>
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
                      {row.dataset.row_count.toLocaleString()} rows
                    </small>
                  </div>
                </div>
                <StatusPill
                  label={row.dataset.status.replaceAll("_", " ")}
                  tone={
                    row.dataset.status === "PROFILE_READY" ? "success" : "info"
                  }
                />
                <div className="overview-dataset-score">
                  {row.score === null ? (
                    <span className="muted">Profile pending</span>
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
              <span className="eyebrow">CATALOG STATUS</span>
              <h3>Readiness distribution</h3>
            </div>
            <span className="panel-caption">
              {approvedRules} approved rules active
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
            <span>Review queue</span>
            <strong>{proposalCount} pending</strong>
          </div>
        </article>
      </section>
      <section className="overview-chart-grid">
        <article className="panel overview-trend-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">ACTIVE DATASET TREND</span>
              <h3>Quality score over time</h3>
            </div>
            <button
              className="text-button"
              onClick={() => onNavigate("visualization")}
            >
              Open full view →
            </button>
          </div>
          <TrendChart points={qualityTrends} />
        </article>
        <article className="panel overview-compare-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">QUALITY COMPARISON</span>
              <h3>Completeness vs validity</h3>
            </div>
            <span className="panel-caption">Profiled datasets only</span>
          </div>
          <OverviewQualityBars rows={qualityRows} />
        </article>
      </section>
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
  return (
    <div className="overview-quality-bars">
      {rows.map((row) => (
        <div className="overview-quality-bar" key={row.dataset.id}>
          <div className="overview-quality-label">
            <strong>{row.dataset.name}</strong>
            <span>
              {row.score === null
                ? "Profile pending"
                : `${row.score.toFixed(1)}% overall`}
            </span>
          </div>
          <div className="overview-quality-lines">
            <div>
              <span>Completeness</span>
              <div className="overview-line-track">
                <i
                  style={{ width: `${row.profile?.completeness_score ?? 0}%` }}
                />
              </div>
              <strong>
                {row.profile
                  ? `${row.profile!.completeness_score.toFixed(1)}%`
                  : "—"}
              </strong>
            </div>
            <div>
              <span>Validity</span>
              <div className="overview-line-track validity">
                <i style={{ width: `${row.profile?.validity_score ?? 0}%` }} />
              </div>
              <strong>
                {row.profile
                  ? `${row.profile!.validity_score.toFixed(1)}%`
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
  const toneColor =
    tone === "green"
      ? "#10b981"
      : tone === "blue"
        ? "#3b82f6"
        : tone === "amber"
          ? "#f59e0b"
          : "#8b5cf6";

  return (
    <div className={`stat-card ${tone}`} style={{ position: "relative", overflow: "hidden" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="stat-label" style={{ fontWeight: 600, fontSize: "11px", color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</span>
        <span className="status-dot" style={{ width: "8px", height: "8px", borderRadius: "50%", backgroundColor: toneColor, display: "inline-block" }} />
      </div>
      <strong style={{ fontSize: "24px", fontWeight: 700, margin: "8px 0 4px 0", display: "block", color: "var(--ink)", letterSpacing: "-0.02em" }}>{value}</strong>
      <span className="stat-detail" style={{ fontSize: "12px", color: "var(--muted)" }}>{detail}</span>
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
  const safeProposals = Array.isArray(proposals) ? proposals : [];
  const safeConfigurations = Array.isArray(configurations) ? configurations : [];

  const pending = safeProposals.filter((proposal) =>
    proposal && ["PROPOSED", "EDITED"].includes(proposal.status),
  );
  const { t } = useI18n();
  const approved = safeProposals.filter(
    (proposal) => proposal && proposal.status === "APPROVED",
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            STEP 3 · {t("wizard.step3Title").toUpperCase()}
          </span>
          <h1>
            {pipelineMode
              ? t("rules.titlePipeline")
              : t("rules.titleHuman")}
          </h1>
          <p>
            {t("rules.subtitle")}
          </p>
        </div>
        <div className="heading-actions">
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
              {t("rules.addManualAnyway")}
            </button>
          )}
        </section>
      ) : !safeProposals.length ? (
        <section className="empty-state">
          <div className="empty-illustration">✦</div>
          <h2>{t("rules.noProposalsTitle")}</h2>
          <p>{t("rules.noProposalsDesc")}</p>
          {canOperate && (
            <div className="dialog-actions">
              <button className="button secondary" onClick={onCreateManual}>
                {t("rules.addManual")}
              </button>
              <button
                className="button primary"
                onClick={onRequestProposals}
                disabled={busy}
              >
                {t("rules.generateProposals")}
              </button>
            </div>
          )}
        </section>
      ) : (
        <>
          <div className="review-summary">
            <div>
              <span className="eyebrow">{t("rules.reviewQueue")}</span>
              <strong>{t("rules.awaitingDecision").replace("{{count}}", pending.length.toString())}</strong>
            </div>
            <div className="review-progress">
              <span
                style={{
                  width: `${safeProposals.length ? ((safeProposals.length - pending.length) / safeProposals.length) * 100 : 0}%`,
                }}
              />
            </div>
            <span>
              {t("rules.approvedSummary")
                .replace("{{approved}}", approved.length.toString())
                .replace(
                  "{{rejected}}",
                  safeProposals.filter((p) => p && p.status === "REJECTED").length.toString(),
                )}
            </span>
          </div>
          <div className="proposal-list">
            {safeProposals.map((proposal) => (
              <ProposalCard
                key={proposal.id}
                proposal={proposal}
                canOperate={canOperate}
                onApprove={() => onApprove(proposal.id)}
                onReject={() => onReject(proposal.id)}
                onEdit={() => onEdit(proposal)}
                onDelete={() => onDelete(proposal.id)}
                configuration={safeConfigurations.find(
                  (item) => item && item.rule_id === proposal.id,
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
  const { t } = useI18n();
  if (!proposal) return null;

  const status = proposal.status || "PROPOSED";
  const pending = ["PROPOSED", "EDITED"].includes(status);
  const editable = pending || status === "APPROVED";
  const canApprove = status !== "APPROVED";
  const canReject = status !== "REJECTED";
  const tone =
    status === "REJECTED"
      ? "danger"
      : status === "APPROVED"
        ? "success"
        : "warning";
  const ruleType = proposal.rule?.type ?? "";
  const severity = proposal.severity ?? "LOW";

  return (
    <article className={`proposal-card ${status.toLowerCase()}`}>
      <div className="proposal-top">
        <div className={`rule-type ${ruleType}`}>
          <span>✦</span>
          {ruleType ? ruleType.replaceAll("_", " ") : "RULE"}
        </div>
        <span className="proposal-source">
          {proposal.source === "MANUAL" ? t("rules.manualSource") : t("rules.agentSource")}
        </span>
        <StatusPill label={status} tone={tone} />
        <span className={`severity ${severity.toLowerCase()}`}>
          {t("rules.severityLevel").replace("{{severity}}", severity)}
        </span>
      </div>
      <div className="proposal-main">
        <div className="proposal-content">
          <h3>{proposal.title || "Untitled Rule"}</h3>
          <p>{proposal.description || ""}</p>
          <div className="rule-code">
            <span>{t("rules.type")}</span>
            <code>{formatRule(proposal.rule)}</code>
          </div>
        </div>
        <div className="confidence">
          <span>{t("rules.confidence")}</span>
          <strong>{Math.round((proposal.confidence ?? 1) * 100)}%</strong>
          <div className="confidence-track">
            <span style={{ width: `${(proposal.confidence ?? 1) * 100}%` }} />
          </div>
        </div>
      </div>
      <div className="evidence-row">
        <span className="evidence-label">{t("rules.evidence")}</span>
        <span>{proposal.evidence_summary || "—"}</span>
        {(proposal.evidence_refs ?? []).map((ref) => (
          <code key={ref}>{ref}</code>
        ))}
      </div>
      {(editable || status === "REJECTED") && canOperate && (
        <div className="proposal-actions">
          {canReject && (
            <button className="button ghost proposal-action reject" onClick={onReject}>
              {status === "APPROVED"
                ? t("rules.rejectApproved")
                : t("rules.reject")}
            </button>
          )}
          <button className="button secondary proposal-action edit" onClick={onEdit}>
            {pending
              ? t("rules.edit")
              : status === "APPROVED"
                ? t("rules.editApproved")
                : t("rules.editRejected")}
          </button>
          {canApprove && (
            <button className="button primary proposal-action approve" onClick={onApprove}>
              {status === "REJECTED"
                ? t("rules.reApprove")
                : t("rules.approveRule")}
            </button>
          )}
          {status !== "APPROVED" && (
            <button className="button ghost proposal-action delete" onClick={onDelete}>
              {t("rules.delete")}
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
  const { t } = useI18n();
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
      ? t("rules.manualOnly")
      : frequency === "HOURLY"
        ? t("rules.hourly")
        : t("rules.daily");
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
          {executionStatus === "ACTIVE" ? t("rules.active") : t("rules.paused")}
        </span>
        <span className="configuration-summary">
          <strong>{t("rules.executionSettings")}</strong>
          <small>
            {frequencyLabel} · {timezone}
          </small>
        </span>
        <span className="configuration-action">
          {expanded ? t("rules.hideOptions") : t("rules.configure")}
          <i aria-hidden="true">⌄</i>
        </span>
      </button>
      {expanded && (
        <div className="rule-settings" id={panelId}>
          <div className="rule-settings-fields">
            <label>
              {t("rules.status")}
              <select
                value={executionStatus}
                onChange={(event) =>
                  setExecutionStatus(
                    event.target.value as RuleConfiguration["execution_status"],
                  )
                }
              >
                <option value="ACTIVE">{t("rules.active")}</option>
                <option value="PAUSED">{t("rules.paused")}</option>
              </select>
            </label>
            <label>
              {t("rules.schedule")}
              <select
                value={frequency}
                onChange={(event) =>
                  setFrequency(
                    event.target
                      .value as RuleConfiguration["schedule_frequency"],
                  )
                }
              >
                <option value="MANUAL">{t("rules.manualOnly")}</option>
                <option value="HOURLY">{t("rules.hourly")}</option>
                <option value="DAILY">{t("rules.daily")}</option>
              </select>
            </label>
            <label>
              {t("rules.timezone")}
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
              {t("rules.saveSettings")}
            </button>
          </div>
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
}: {
  activeRun: DqRun | null;
  results: DqResult[];
  anomalies: DqAnomaly[];
  approvedCount: number;
  busy: boolean;
  canOperate: boolean;
  onRun: () => void;
}) {
  const { t } = useI18n();
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t("runs.eyebrow")}</span>
          <h1>{t("runs.title")}</h1>
          <p>
            {t("runs.subtitle")}
          </p>
        </div>
      </div>
      {!activeRun ? (
        <section className="empty-state">
          <div className="empty-illustration">↗</div>
          <h2>{t("runs.noRunTitle")}</h2>
          <p>
            {t("runs.noRunDesc")}
          </p>
          {canOperate && (
            <button
              className="button primary"
              onClick={onRun}
              disabled={!approvedCount || busy}
            >
              {t("runs.runApproved")}
            </button>
          )}
        </section>
      ) : (
        <>
          <div className="run-hero">
            <div>
              <span className="eyebrow">{t("runs.latestRun")}</span>
              <h2>{activeRun.id}</h2>
              <p>
                {t("runs.created")
                  .replace("{{time}}", formatTime(activeRun.created_at))
                  .replace("{{count}}", activeRun.rule_ids.length.toString())}
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
                label={t("runs.checkedRows")}
                value={activeRun.total_checked.toLocaleString()}
                detail={t("runs.checkedRowsDetail")}
                tone="blue"
              />
              <StatCard
                label={t("runs.failedRows")}
                value={activeRun.total_failed.toLocaleString()}
                detail={t("runs.failedRowsDetail")}
                tone="amber"
              />
              <StatCard
                label={t("runs.rulesExecuted")}
                value={`${activeRun.rule_ids.length}`}
                detail={t("runs.rulesExecutedDetail")}
                tone="green"
              />
              <StatCard
                label={t("runs.rawValues")}
                value="0"
                detail={t("runs.rawValuesDetail")}
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
                  <span className="eyebrow">{t("runs.anomalyDetection")}</span>
                  <h3>
                    {anomalies.length
                      ? t("runs.signalsAttention")
                      : t("runs.noAnomalousShifts")}
                  </h3>
                </div>
                <StatusPill
                  label={
                    anomalies.length ? t("runs.anomaliesDetected").replace("{{count}}", anomalies.length.toString()) : t("runs.clear")
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
                            ? t("runs.historicalSpike")
                            : t("runs.highFailureRate")}
                        </span>
                      </div>
                      <div className="anomaly-metrics">
                        <div>
                          <small>{t("runs.current")}</small>
                          <strong>
                            {(anomaly.current_rate * 100).toFixed(2)}%
                          </strong>
                        </div>
                        <div>
                          <small>{t("runs.baseline")}</small>
                          <strong>
                            {anomaly.historical_mean == null
                              ? t("runs.coldStart")
                              : `${(anomaly.historical_mean * 100).toFixed(2)}%`}
                          </strong>
                        </div>
                        <div>
                          <small>{t("runs.zScore")}</small>
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
                <span className="eyebrow">{t("runs.resultsTitle")}</span>
                <h3>{t("runs.ruleOutcomes")}</h3>
              </div>
              <span className="panel-caption">{results.length} checks</span>
            </div>
            {results.length ? (
              <div className="results-table">
                <div className="result-header">
                  <span>{t("runs.ruleHeader")}</span>
                  <span>{t("runs.statusHeader")}</span>
                  <span>{t("runs.checkedHeader")}</span>
                  <span>{t("runs.failedHeader")}</span>
                  <span>{t("runs.failedIdsHeader")}</span>
                </div>
                {results.map((result) => (
                  <div className="result-row" key={result.rule_id}>
                    <strong>{result.rule_title}</strong>
                    <StatusPill
                      label={result.status}
                      tone={result.status === "PASS" ? "success" : "danger"}
                    />
                    <span>{result.checked_count.toLocaleString()}</span>
                    <strong
                      className={result.failed_count ? "metric-warn" : ""}
                    >
                      {result.failed_count.toLocaleString()}
                    </strong>
                    <code>
                      {result.failed_row_ids.length
                        ? result.failed_row_ids.join(", ")
                        : "—"}
                    </code>
                  </div>
                ))}
              </div>
            ) : (
              <div className="table-empty">
                The runner is preparing bounded results…
              </div>
            )}
          </div>
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
  const historicalReady = trends.length >= 6;
  const detectionMode =
    anomalies[0]?.detection_mode === "HISTORICAL" ||
      (!anomalies.length && historicalReady)
      ? "Historical baseline"
      : "Cold-start screen";
  return (
    <article className="panel anomaly-monitor">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">AUTOMATED ANOMALY DETECTION</span>
          <h3>Violation-rate monitoring</h3>
        </div>
        <StatusPill
          label={
            anomalies.length
              ? `${anomalies.length} SIGNAL${anomalies.length === 1 ? "" : "S"}`
              : "NO SIGNALS"
          }
          tone={anomalies.length ? "warning" : "success"}
        />
      </div>
      <div className="anomaly-monitor-layout">
        <div className="anomaly-engine">
          <span className="monitor-label">WHEN IT RUNS</span>
          <strong>After every completed DQ run</strong>
          <p>
            It compares each approved rule’s failure rate without reading raw
            values in the browser.
          </p>
          <div className="anomaly-engine-state">
            <i />
            <span>{detectionMode}</span>
          </div>
        </div>
        <div className="anomaly-evaluation">
          <div className="anomaly-spec-grid">
            <div>
              <span>Minimum sample</span>
              <strong>100 rows</strong>
              <small>small checks are ignored</small>
            </div>
            <div>
              <span>Cold start</span>
              <strong>≥ 5.0%</strong>
              <small>until 5 prior runs exist</small>
            </div>
            <div>
              <span>Historical mode</span>
              <strong>z ≥ 2.5</strong>
              <small>also requires rate &gt; 1.0%</small>
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
                        ? "Historical spike"
                        : "High violation rate"}
                    </span>
                  </div>
                  <div className="anomaly-monitor-metrics">
                    <span>
                      Current{" "}
                      <strong>
                        {(anomaly.current_rate * 100).toFixed(2)}%
                      </strong>
                    </span>
                    <span>
                      {anomaly.historical_mean == null ? (
                        "Baseline unavailable"
                      ) : (
                        <>
                          Baseline{" "}
                          <strong>
                            {(anomaly.historical_mean * 100).toFixed(2)}%
                          </strong>
                        </>
                      )}
                    </span>
                    <span>
                      {anomaly.z_score == null ? (
                        `${anomaly.history_size} prior runs`
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
              <span>Latest evaluation</span>
              <strong>No unusual violation-rate movement detected.</strong>
              <p>
                {historicalReady
                  ? "Current rule rates remain within their stored historical baselines."
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
  const { t } = useI18n();

  // Composite Health Score calculation
  const completeness = profile?.completeness_score ?? 100;
  const validity = profile?.validity_score ?? 100;
  const uniqueness = Math.max(0, 100 - (profile?.duplicate_rate ?? 0));
  const healthScore = Math.round(completeness * 0.4 + validity * 0.3 + uniqueness * 0.3);

  const healthGrade =
    healthScore >= 90 ? "A+" : healthScore >= 80 ? "A" : healthScore >= 70 ? "B" : "C";
  const healthTone =
    healthScore >= 90 ? "green" : healthScore >= 75 ? "amber" : "warn";

  const sortedColumns = [...(profile?.columns ?? [])]
    .sort((a, b) => b.null_rate - a.null_rate);

  const columnsWithNulls = sortedColumns.filter((c) => c.null_rate > 0);
  const totalColumns = profile?.columns.length ?? 0;

  const circumference = 2 * Math.PI * 52;
  const scoreOffset = circumference * (1 - Math.min(100, Math.max(0, healthScore)) / 100);

  return (
    <div style={{ marginTop: "16px" }}>
      <div className="page-heading visualization-heading" style={{ background: "var(--surface-card, #ffffff)", padding: "24px", borderRadius: "12px", border: "1px solid var(--border, #e2e8f0)", marginBottom: "24px" }}>
        <div>
          <span className="eyebrow" style={{ color: "var(--primary, #2563eb)", fontWeight: 700 }}>
            {t("overview.eyebrow") || "DATA QUALITY OBSERVATORY"}
          </span>
          <h2 style={{ fontSize: "22px", margin: "6px 0" }}>
            📊 {t("overview.title") || "Báo cáo Tổng quan Sức khỏe Dữ liệu"}
          </h2>
          <p className="muted" style={{ fontSize: "14px", maxWidth: "600px" }}>
            Đánh giá tự động tính đầy đủ, hợp lệ và trùng lặp của tập dữ liệu để phát hiện rủi ro trước khi xây dựng quy tắc.
          </p>
        </div>

        <div className="quality-dial" aria-label={`Quality score ${healthScore}%`}>
          <svg viewBox="0 0 120 120" aria-hidden="true">
            <circle cx="60" cy="60" r="52" className="quality-dial-track" />
            <circle
              cx="60"
              cy="60"
              r="52"
              className="quality-dial-progress"
              stroke={healthScore >= 90 ? "#10b981" : healthScore >= 75 ? "#f59e0b" : "#ef4444"}
              strokeDasharray={circumference}
              strokeDashoffset={scoreOffset}
            />
          </svg>
          <div>
            <strong>{healthScore}%</strong>
            <span style={{ fontSize: "11px", textTransform: "uppercase" }}>Grade {healthGrade}</span>
          </div>
        </div>
      </div>

      {/* 4 Health KPI Cards */}
      <section className="visual-kpi-rail" style={{ gridTemplateColumns: "repeat(4, 1fr)", gap: "16px", marginBottom: "24px" }}>
        <div style={{ padding: "18px", borderRadius: "10px", background: "var(--surface-card, #fff)", border: "1px solid var(--border, #e2e8f0)" }}>
          <span className="muted" style={{ fontSize: "12px", textTransform: "uppercase" }}>Tổng số dòng dữ liệu</span>
          <strong style={{ fontSize: "22px", display: "block", margin: "4px 0" }}>{(profile?.row_count ?? 0).toLocaleString()}</strong>
          <small className="muted">bản ghi đã phân tích</small>
        </div>
        <div style={{ padding: "18px", borderRadius: "10px", background: "var(--surface-card, #fff)", border: "1px solid var(--border, #e2e8f0)" }}>
          <span className="muted" style={{ fontSize: "12px", textTransform: "uppercase" }}>Chỉ số đầy đủ (Completeness)</span>
          <strong style={{ fontSize: "22px", display: "block", margin: "4px 0", color: completeness < 95 ? "#f59e0b" : "#10b981" }}>
            {completeness.toFixed(1)}%
          </strong>
          <small className="muted">{columnsWithNulls.length} cột có giá trị thiếu</small>
        </div>
        <div style={{ padding: "18px", borderRadius: "10px", background: "var(--surface-card, #fff)", border: "1px solid var(--border, #e2e8f0)" }}>
          <span className="muted" style={{ fontSize: "12px", textTransform: "uppercase" }}>Tỷ lệ không trùng lặp</span>
          <strong style={{ fontSize: "22px", display: "block", margin: "4px 0", color: profile && profile!.duplicate_rate > 0 ? "#f59e0b" : "#10b981" }}>
            {uniqueness.toFixed(2)}%
          </strong>
          <small className="muted">{profile?.duplicate_rate ?? 0}% trùng lặp dòng</small>
        </div>
        <div style={{ padding: "18px", borderRadius: "10px", background: "var(--surface-card, #fff)", border: "1px solid var(--border, #e2e8f0)" }}>
          <span className="muted" style={{ fontSize: "12px", textTransform: "uppercase" }}>Đánh giá rủi ro (Risk Status)</span>
          <strong style={{ fontSize: "20px", display: "block", margin: "4px 0", color: healthTone === "green" ? "#10b981" : healthTone === "amber" ? "#d97706" : "#dc2626" }}>
            {healthScore >= 90 ? "🟢 An toàn (Healthy)" : healthScore >= 75 ? "🟡 Cần lưu ý (Notice)" : "🔴 Rủi ro cao (High Risk)"}
          </strong>
          <small className="muted">Sẵn sàng chuyển sang Step 3</small>
        </div>
      </section>

      {/* Main Observatory Grid */}
      <section className="visual-grid" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "24px" }}>
        {/* Panel 1: Column Completeness Health Bars */}
        <article className="panel completeness-panel" style={{ padding: "20px", background: "#fff", borderRadius: "10px", border: "1px solid var(--border, #e2e8f0)" }}>
          <div className="panel-heading" style={{ marginBottom: "16px" }}>
            <div>
              <span className="eyebrow" style={{ fontSize: "11px" }}>PROFILE HEALTH</span>
              <h3 style={{ fontSize: "16px", margin: "2px 0" }}>Phân tích độ đầy đủ theo cột ({totalColumns} cột)</h3>
            </div>
            <span className="panel-caption muted" style={{ fontSize: "12px" }}>Xếp theo tỷ lệ khuyết dữ liệu</span>
          </div>
          <div className="viz-bars" style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
            {sortedColumns.slice(0, 8).map((column) => {
              const compRate = Math.max(0, 100 - column.null_rate * 100);
              const barColor = compRate === 100 ? "#10b981" : compRate > 80 ? "#f59e0b" : "#ef4444";
              return (
                <div className="viz-bar-row" key={column.name} style={{ display: "grid", gridTemplateColumns: "140px 1fr 60px", alignItems: "center", gap: "12px" }}>
                  <span style={{ fontWeight: 600, fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{column.name}</span>
                  <div style={{ background: "#f1f5f9", height: "10px", borderRadius: "5px", overflow: "hidden" }}>
                    <i style={{ display: "block", height: "100%", width: `${compRate}%`, background: barColor, borderRadius: "5px", transition: "width 0.3s" }} />
                  </div>
                  <strong style={{ fontSize: "12px", textAlign: "right" }}>{compRate.toFixed(1)}%</strong>
                </div>
              );
            })}
            {!profile && (
              <div className="chart-empty muted" style={{ textAlign: "center", padding: "20px" }}>
                Chưa có dữ liệu hồ sơ. Bấm "Start Quality Profiling" ở trên để phân tích.
              </div>
            )}
          </div>
        </article>

        {/* Panel 2: Diagnostics & Recommended Actions */}
        <article className="panel signal-summary" style={{ padding: "20px", background: "#fff", borderRadius: "10px", border: "1px solid var(--border, #e2e8f0)", display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
          <div>
            <div className="signal-heading" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <span className="eyebrow" style={{ fontSize: "11px" }}>DIAGNOSTIC SUMMARY</span>
              <span className={`status-pill ${healthTone === "green" ? "success" : "warning"}`}>
                {healthScore >= 80 ? "Sẵn sàng sinh quy tắc" : "Cần kiểm tra lại dữ liệu"}
              </span>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginBottom: "20px" }}>
              <div style={{ background: "var(--surface-muted, #f8fafc)", padding: "12px 16px", borderRadius: "8px", borderLeft: "4px solid #2563eb" }}>
                <strong style={{ fontSize: "13px", display: "block", color: "#1e293b" }}>💡 Ý nghĩa chỉ số Observatory:</strong>
                <p style={{ margin: "4px 0 0 0", fontSize: "12px", color: "#64748b" }}>
                  Bảng điều khiển này giúp bạn phát hiện các cột trống (nulls), cột bị trùng lặp hoặc vi phạm kiểu dữ liệu ngay khi vừa nạp dữ liệu.
                </p>
              </div>

              {columnsWithNulls.length > 0 ? (
                <div style={{ background: "#fffbeb", padding: "12px 16px", borderRadius: "8px", borderLeft: "4px solid #f59e0b" }}>
                  <strong style={{ fontSize: "13px", color: "#b45309" }}>⚠️ Phát hiện {columnsWithNulls.length} cột có dữ liệu khuyết:</strong>
                  <p style={{ margin: "4px 0 0 0", fontSize: "12px", color: "#78350f" }}>
                    Các cột: {columnsWithNulls.map((c) => c.name).join(", ")}. AI Agent ở Step 3 sẽ tự động tạo quy tắc <code>NOT NULL</code> để kiểm soát các cột này.
                  </p>
                </div>
              ) : (
                <div style={{ background: "#f0fdf4", padding: "12px 16px", borderRadius: "8px", borderLeft: "4px solid #10b981" }}>
                  <strong style={{ fontSize: "13px", color: "#15803d" }}>✓ Tất cả các cột đều hoàn chỉnh 100%:</strong>
                  <p style={{ margin: "4px 0 0 0", fontSize: "12px", color: "#166534" }}>
                    Không phát hiện ô trống (null values) trong bộ dữ liệu này.
                  </p>
                </div>
              )}
            </div>
          </div>

          <div style={{ background: "#eff6ff", padding: "16px", borderRadius: "8px", border: "1px solid #bfdbfe", marginTop: "auto" }}>
            <h4 style={{ margin: "0 0 6px 0", fontSize: "14px", color: "#1e40af" }}>🎯 Khuyến nghị thao tác:</h4>
            <p style={{ margin: "0", fontSize: "13px", color: "#1e3a8a" }}>
              Hồ sơ dữ liệu đã hợp lệ. Hãy bấm <strong>Tiếp tục (Next) →</strong> ở góc dưới để chuyển sang <strong>Step 3 (Sinh & Duyệt quy tắc)</strong>, nơi AI Agent sẽ tự động chuyển đổi các phát hiện này thành quy tắc kiểm thử có thể thực thi.
            </p>
          </div>
        </article>
      </section>
    </div>
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

function DataExplorerPage({ dataset }: { dataset?: Dataset }) {
  const [query, setQuery] = useState<DatasetRowQuery>({
    quality_status: "ALL",
    sort_by: "pickup_at",
    sort_direction: "desc",
    limit: 25,
    offset: 0,
  });
  const [response, setResponse] = useState<DatasetRowsResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [queryError, setQueryError] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);

  const loadRows = useCallback(
    async (nextQuery: DatasetRowQuery) => {
      if (!dataset) return;
      setBusy(true);
      setQueryError("");
      try {
        setResponse(await api.queryDatasetRows(dataset.id, nextQuery));
        setQuery(nextQuery);
      } catch (requestError) {
        setQueryError(
          getErrorMessage(requestError, "Unable to query dataset rows."),
        );
      } finally {
        setBusy(false);
      }
    },
    [dataset],
  );

  useEffect(() => {
    if (dataset) void loadRows(query);
  }, [dataset, loadRows]);
  const page = response ? Math.floor(response.offset / response.limit) + 1 : 1;
  const pageCount = response
    ? Math.max(1, Math.ceil(response.total / response.limit))
    : 1;
  const updateFilter = (
    key: keyof DatasetRowQuery,
    value: string | number | undefined,
  ) => setQuery((current) => ({ ...current, [key]: value, offset: 0 }));
  const activeFilterCount = [
    query.quality_status !== "ALL",
    Boolean(query.vendor_id),
    Boolean(query.payment_type),
    query.min_distance !== undefined,
    query.max_distance !== undefined,
    query.sort_by !== "pickup_at",
  ].filter(Boolean).length;
  const filterSummary = [
    query.quality_status === "ALL"
      ? "All rows"
      : query.quality_status === "ISSUE"
        ? "Issues only"
        : "Valid only",
    query.vendor_id ? `Vendor: ${query.vendor_id}` : "Any vendor",
    query.payment_type ? `Payment: ${query.payment_type}` : "Any payment",
  ].join(" · ");

  return (
    <>
      <div className="page-heading data-explorer-heading">
        <div>
          <span className="eyebrow">BOUNDED READ ACCESS</span>
          <h1>Data explorer</h1>
          <p>
            Inspect a safe field projection with server-side filters and
            pagination.
          </p>
        </div>
        <span className="data-count">
          {response?.total.toLocaleString() ?? "—"}
          <small>matching rows</small>
        </span>
      </div>
      <section
        className={`panel filter-panel ${filtersOpen ? "is-open" : "is-collapsed"}`}
      >
        <div className="filter-toolbar">
          <div className="filter-toolbar-copy">
            <span className="eyebrow">QUERY CONTROLS</span>
            <button
              type="button"
              className="filter-toggle"
              aria-expanded={filtersOpen}
              aria-controls="data-explorer-filters"
              onClick={() => setFiltersOpen((open) => !open)}
            >
              <span className="filter-toggle-icon" aria-hidden="true">
                {filtersOpen ? "−" : "+"}
              </span>
              <span>{filtersOpen ? "Hide filters" : "Filter rows"}</span>
            </button>
            {!filtersOpen && (
              <span className="filter-summary">{filterSummary}</span>
            )}
          </div>
          <div className="filter-toolbar-state">
            <span
              className={
                activeFilterCount
                  ? "filter-active-count"
                  : "filter-default-state"
              }
            >
              {activeFilterCount
                ? `${activeFilterCount} active`
                : "Default view"}
            </span>
            <span>Read-only</span>
          </div>
        </div>
        {filtersOpen && (
          <form
            id="data-explorer-filters"
            onSubmit={(event) => {
              event.preventDefault();
              void loadRows({ ...query, offset: 0 });
            }}
          >
            <label>
              Quality
              <select
                value={query.quality_status}
                onChange={(event) =>
                  updateFilter("quality_status", event.target.value)
                }
              >
                <option value="ALL">All rows</option>
                <option value="ISSUE">Issues only</option>
                <option value="VALID">Valid only</option>
              </select>
            </label>
            <label>
              Vendor
              <select
                value={query.vendor_id ?? ""}
                onChange={(event) =>
                  updateFilter("vendor_id", event.target.value || undefined)
                }
              >
                <option value="">Any vendor</option>
                <option>Curb Mobility, LLC</option>
                <option>Creative Mobile Technologies, LLC</option>
                <option>Unknown Vendor</option>
              </select>
            </label>
            <label>
              Payment
              <select
                value={query.payment_type ?? ""}
                onChange={(event) =>
                  updateFilter("payment_type", event.target.value || undefined)
                }
              >
                <option value="">Any payment</option>
                <option>Flex Fare trip</option>
                <option>Credit card</option>
                <option>Cash</option>
                <option>No charge</option>
                <option>Dispute</option>
                <option>Invalid Payment (Dispute/Test)</option>
              </select>
            </label>
            <label>
              Min distance
              <input
                type="number"
                step="0.1"
                value={query.min_distance ?? ""}
                onChange={(event) =>
                  updateFilter(
                    "min_distance",
                    event.target.value === ""
                      ? undefined
                      : Number(event.target.value),
                  )
                }
                placeholder="No minimum"
              />
            </label>
            <label>
              Max distance
              <input
                type="number"
                step="0.1"
                value={query.max_distance ?? ""}
                onChange={(event) =>
                  updateFilter(
                    "max_distance",
                    event.target.value === ""
                      ? undefined
                      : Number(event.target.value),
                  )
                }
                placeholder="No maximum"
              />
            </label>
            <label>
              Sort by
              <select
                value={query.sort_by}
                onChange={(event) =>
                  updateFilter("sort_by", event.target.value)
                }
              >
                <option value="pickup_at">Pickup time</option>
                <option value="trip_distance">Distance</option>
                <option value="fare_amount">Fare</option>
                <option value="total_amount">Total</option>
              </select>
            </label>
            <button className="button primary filter-apply" disabled={busy}>
              {busy ? "Querying…" : "Apply filters"}
            </button>
          </form>
        )}
        {filtersOpen && (
          <div className="filter-note">
            Read-only · up to 100 rows
          </div>
        )}
      </section>
      {queryError && (
        <div className="alert error">
          <strong>Query failed</strong>
          <span>{queryError}</span>
        </div>
      )}
      <section className="panel data-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">QUERY RESULT</span>
            <h3>Dataset rows</h3>
          </div>
          <span className="panel-caption">
            page {page} / {pageCount}
          </span>
        </div>
        {busy && !response ? (
          <div className="data-skeleton">
            Loading bounded dataset projection…
          </div>
        ) : response?.rows.length ? (
          <div className="data-table-wrap">
            <table className="data-table">
              <colgroup>
                <col className="data-col-status" />
                <col className="data-col-row-id" />
                <col className="data-col-pickup" />
                <col className="data-col-vendor" />
                <col className="data-col-payment" />
                <col className="data-col-number" />
                <col className="data-col-number" />
                <col className="data-col-number" />
              </colgroup>
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Row ID</th>
                  <th>Pickup</th>
                  <th>Vendor</th>
                  <th>Payment</th>
                  <th>Distance</th>
                  <th>Fare</th>
                  <th>Total</th>
                </tr>
              </thead>
              <tbody>
                {response.rows.map((row) => {
                  const issue = rowHasQualityIssue(row);
                  return (
                    <tr key={row.source_row_id}>
                      <td>
                        <StatusPill
                          label={issue ? "ISSUE" : "VALID"}
                          tone={issue ? "warning" : "success"}
                        />
                      </td>
                      <td>
                        <code>{row.source_row_id}</code>
                      </td>
                      <td
                        title={
                          row.pickup_at
                            ? new Date(row.pickup_at).toLocaleString()
                            : undefined
                        }
                      >
                        {row.pickup_at
                          ? new Date(row.pickup_at).toLocaleString()
                          : "—"}
                      </td>
                      <td title={row.vendor_id}>{row.vendor_id ?? "—"}</td>
                      <td title={row.payment_type}>
                        {row.payment_type ?? "—"}
                      </td>
                      <td
                        className={
                          (row.trip_distance ?? 0) < 0 ? "metric-warn" : ""
                        }
                      >
                        {row.trip_distance?.toFixed(2) ?? "—"}
                      </td>
                      <td
                        className={
                          (row.fare_amount ?? 0) < 0 ? "metric-warn" : ""
                        }
                      >
                        {row.fare_amount?.toFixed(2) ?? "—"}
                      </td>
                      <td>{row.total_amount?.toFixed(2) ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="table-empty">No rows match the current filters.</div>
        )}
        <div className="pagination">
          <button
            className="button ghost"
            disabled={!response || response.offset === 0 || busy}
            onClick={() =>
              void loadRows({
                ...query,
                offset: Math.max(
                  0,
                  (response?.offset ?? 0) - (response?.limit ?? 25),
                ),
              })
            }
          >
            ← Previous
          </button>
          <span>
            {response
              ? `${response.offset + 1}–${Math.min(response.offset + response.limit, response.total)} of ${response.total.toLocaleString()}`
              : "No result"}
          </span>
          <button
            className="button ghost"
            disabled={
              !response ||
              response.offset + response.limit >= response.total ||
              busy
            }
            onClick={() =>
              void loadRows({
                ...query,
                offset: (response?.offset ?? 0) + (response?.limit ?? 25),
              })
            }
          >
            Next →
          </button>
        </div>
      </section>
    </>
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
  const { t } = useI18n();
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t("audit.eyebrow")}</span>
          <h1>{t("audit.title")}</h1>
          <p>
            {t("audit.subtitle")}
          </p>
        </div>
        <StatusPill label={t("audit.auditEnabled")} tone="success" />
      </div>
      <div className="panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("audit.eventStream")}</span>
            <h3>{t("audit.recentActivity")}</h3>
          </div>
          <span className="panel-caption">{logs.length} events</span>
        </div>
        {logs.length ? (
          <div className="audit-list">
            {logs.map((log) => (
              <div className="audit-row" key={log.id}>
                <div className="audit-icon">✓</div>
                <div>
                  <strong>{log.summary}</strong>
                  <span>
                    {log.action} · {log.entity_type} · {log.actor}
                  </span>
                </div>
                <time>{formatTime(log.created_at)}</time>
              </div>
            ))}
          </div>
        ) : (
          <div className="table-empty">{t("audit.noEvents")}</div>
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
  const { t } = useI18n();
  const safeRule = rule ?? { type: "not_null" };
  const type = safeRule.type ?? "not_null";
  const update = (patch: Partial<RuleSpec>) => onChange({ ...safeRule, ...patch });
  const csv = (values: string[] | undefined) => (values ?? []).join(", ");
  const parseCsv = (value: string) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  return (
    <div className="rule-editor">
      <span className="eyebrow">{t("rules.type")}</span>
      <div className="rule-type-readonly">
        <strong>{type ? type.replaceAll("_", " ") : "RULE"}</strong>
        <code>{formatRule(safeRule)}</code>
      </div>
      {type === "not_null" && (
        <label>
          Column
          <input
            value={safeRule.column ?? ""}
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
  const { t } = useI18n();
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
            <h2>{t("rules.manualDialogTitle") || "Thêm quy tắc thủ công"}</h2>
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
          {t("rules.manualDialogSubtitle") || "Tạo quy tắc tùy chỉnh. Quy tắc sẽ nằm trong hàng đợi duyệt trước khi thực thi."}
        </p>
        <label>
          {t("rules.ruleTitle") || "Tiêu đề quy tắc"}
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="VD: Giá trị kho bãi phải khác rỗng"
          />
        </label>
        <label>
          {t("rules.ruleDescription") || "Mô tả quy tắc"}
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            placeholder="Giải thích kỳ vọng về chất lượng dữ liệu"
          />
        </label>
        <label>
          {t("rules.ruleSeverity") || "Mức độ nghiêm trọng"}
          <select
            value={severity}
            onChange={(event) =>
              setSeverity(event.target.value as RuleProposal["severity"])
            }
          >
            <option value="LOW">LOW</option>
            <option value="MEDIUM">MEDIUM</option>
            <option value="HIGH">HIGH</option>
          </select>
        </label>
        <label>
          {t("rules.ruleType") || "Loại quy tắc"}
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
            {t("rules.cancel") || "Hủy"}
          </button>
          <button
            className="button primary"
            disabled={!title.trim() || !description.trim()}
            onClick={() => onSave({ title, description, severity, rule })}
          >
            {t("rules.createRule") || "Tạo quy tắc"}
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
  const { t } = useI18n();
  const [title, setTitle] = useState(proposal?.title ?? "");
  const [description, setDescription] = useState(proposal?.description ?? "");
  const [severity, setSeverity] = useState<RuleProposal["severity"]>(proposal?.severity ?? "MEDIUM");
  const [rule, setRule] = useState<RuleSpec>(() => {
    const safe = proposal?.rule ?? { type: "not_null" };
    return {
      ...safe,
      columns: safe.columns ? [...safe.columns] : undefined,
      allowed_values: safe.allowed_values ? [...safe.allowed_values] : undefined,
      fingerprint_columns: safe.fingerprint_columns
        ? [...safe.fingerprint_columns]
        : undefined,
    };
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
            <h2>{t("rules.editDialogTitle") || "Chỉnh sửa quy tắc"}</h2>
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
          {t("rules.editDialogSubtitle") || "Cập nhật thông số và mô tả quy tắc."}
        </p>
        <label>
          {t("rules.ruleTitle") || "Tiêu đề quy tắc"}
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label>
          {t("rules.ruleDescription") || "Mô tả quy tắc"}
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={4}
          />
        </label>
        <label>
          {t("rules.ruleSeverity") || "Mức độ nghiêm trọng"}
          <select
            value={severity}
            onChange={(event) =>
              setSeverity(event.target.value as RuleProposal["severity"])
            }
          >
            <option value="LOW">LOW</option>
            <option value="MEDIUM">MEDIUM</option>
            <option value="HIGH">HIGH</option>
          </select>
        </label>
        <RuleSpecEditor rule={rule} onChange={setRule} />
        <div className="dialog-actions">
          <button className="button ghost" onClick={onClose}>
            {t("rules.cancel") || "Hủy"}
          </button>
          <button
            className="button primary"
            disabled={!title.trim() || !description.trim()}
            onClick={() => onSave({ title, description, severity, rule })}
          >
            {t("rules.saveChanges") || "Lưu thay đổi"}
          </button>
        </div>
      </section>
    </div>
  );
}

export default App;
