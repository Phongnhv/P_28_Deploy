import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, isMockMode, workflowApi } from "./api";
import { ApiError, clearApiSession } from "./api/client";
import ThemeControl from "./ThemeControl";
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
  WorkflowStepKey,
} from "./types";

type View = "overview" | "workflow" | "datasets" | "rules" | "runs" | "visualization" | "data" | "audit" | "admin";

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

function getErrorMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiError))
    return error instanceof Error ? error.message : fallback;
  if (error.status === 401)
    return "Your session has expired. Please sign in again.";
  if (error.status === 409)
    return "This operation is already in progress. Refresh the workspace before retrying.";
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
    <div className="progress-panel">
      <div className="progress-heading">
        <div>
          <span className="eyebrow">ACTIVE JOB</span>
          <h3>{title}</h3>
        </div>
        <strong>{job.progress}%</strong>
      </div>
      <div className="progress-track">
        <span style={{ width: `${job.progress}%` }} />
      </div>
      <div className="progress-meta">
        <span>{job.message}</span>
        <span>{job.status}</span>
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
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
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

const workflowStepLabels: Record<WorkflowStepKey, { label: string; owner: string; description: string }> = {
  UPLOAD_PROFILE: { label: "Prepare dataset", owner: "System", description: "Internal system step: register the dataset and build its deterministic aggregate profile before Agent 1 starts." },
  UNDERSTAND_DATA: { label: "Understand data", owner: "Agent 1", description: "Turn the deterministic profile, schema and metadata into a semantic contract." },
  PROPOSE_RULES: { label: "Propose rules", owner: "Agent 1", description: "Generate typed rules with evidence and confidence." },
  REVIEW_RULES: { label: "Review rules", owner: "Steward", description: "Approve, request changes or reject the rule set." },
  PROPOSE_CODE: { label: "Propose standardization", owner: "Agent 2", description: "Create a deterministic code or transformation plan." },
  REVIEW_EXECUTE: { label: "Review and execute", owner: "Steward", description: "Validate the code proposal before a bounded run." },
  ANALYZE_IMPROVE: { label: "Analyze and improve", owner: "Loop Agent", description: "Explain results and propose a bounded next iteration." },
};

function DatasetsPage({
  datasets,
  dataset,
  onOpenExplorer,
}: {
  datasets: Dataset[];
  dataset?: Dataset;
  onOpenExplorer: (datasetId: string) => void;
}) {
  return <div className="datasets-page"><div className="page-heading datasets-heading"><div><span className="eyebrow">DATASET CATALOG</span><h1>Registered datasets</h1><p>Browse the registered artifacts and open a read-only data view.</p></div><span className="panel-caption">{datasets.length} registered</span></div>{datasets.length ? <div className="dataset-catalog-grid">{datasets.map((item) => <article className={`dataset-catalog-card ${item.id === dataset?.id ? "active" : ""}`} key={item.id}><div className="dataset-catalog-top"><StatusPill label={item.status.replaceAll("_", " ")} tone={item.status === "PROFILE_READY" ? "success" : "info"} /><code>{item.manifest_version}</code></div><h2>{item.name}</h2><p>{item.description}</p><div className="dataset-catalog-stats"><div><span>Rows</span><strong>{item.row_count.toLocaleString()}</strong></div><div><span>Source</span><strong>{item.source_label}</strong></div><div><span>Updated</span><strong>{formatTime(item.updated_at)}</strong></div></div><div className="dataset-catalog-actions"><span className="dataset-catalog-hint">Read-only preview</span><button className="button primary" onClick={() => onOpenExplorer(item.id)}>View data <span aria-hidden="true">→</span></button></div></article>)}</div> : <div className="empty-state"><h2>No datasets registered.</h2><p className="muted">Registered artifacts will appear here when they are available.</p></div>}</div>;
}

function workflowArtifactForStep(workflow: WorkflowRun, artifacts: AgentArtifact[], step: WorkflowStepKey) {
  const ids = workflow.steps.find((item) => item.key === step)?.artifact_ids ?? [];
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
  canOperate: boolean;
  onStartStep: (step: WorkflowStepKey) => void;
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
  onUploadPreview: (fileName: string) => void;
  onBackToDatasetSelection: () => void;
}) {
  const [ruleDatasetId, setRuleDatasetId] = useState(dataset?.id ?? "");
  useEffect(() => {
    setRuleDatasetId(workflow?.dataset_id ?? dataset?.id ?? "");
  }, [workflow?.dataset_id, dataset?.id]);
  if (!workflow) {
    const selectionStepKeys: WorkflowStepKey[] = ["UNDERSTAND_DATA", "PROPOSE_RULES", "REVIEW_RULES", "PROPOSE_CODE", "REVIEW_EXECUTE", "ANALYZE_IMPROVE"];
    const selectedRuleDataset = datasets.find((item) => item.id === ruleDatasetId);
    return <section className="workflow-page"><header className="workflow-page-header"><div><span className="eyebrow">RULE PROPOSER WORKFLOW</span><h1>Dataset to decision</h1><p>{selectedRuleDataset?.name ?? "Choose a registered dataset"} · select the input before starting Agent 1</p></div><span className="status-pill">{selectedRuleDataset ? "READY" : "SELECT DATASET"}</span></header><div className="workflow-layout"><aside className="workflow-stepper" aria-label="Workflow steps"><button type="button" className="workflow-step workflow-step-choice active" aria-current="step"><div className="workflow-step-index">1</div><div><strong>Choose dataset</strong><span>Rule Proposer</span><small>READY</small></div></button>{selectionStepKeys.map((key, index) => <button type="button" className="workflow-step locked" key={key} disabled><div className="workflow-step-index">{index + 2}</div><div><strong>{workflowStepLabels[key].label}</strong><span>{workflowStepLabels[key].owner}</span></div></button>)}</aside><section className="workflow-detail panel workflow-selection-detail"><div className="workflow-detail-heading"><div><span className="eyebrow">CURRENT STEP · 1 OF 7</span><h2>Choose dataset</h2><p>Select a registered dataset for Agent 1 to understand and propose quality rules.</p></div><span className="status-pill">{selectedRuleDataset ? "DATASET SELECTED" : "READY"}</span></div><div className="dataset-selection-holder"><div className="section-heading"><div><span className="eyebrow">REGISTERED DATASETS</span><h3>Select an input</h3></div><span className="muted">{datasets.length} available</span></div><div className="dataset-choice-list">{datasets.map((item) => <button type="button" className={`dataset-choice ${item.id === ruleDatasetId ? "selected" : ""}`} key={item.id} onClick={() => { setRuleDatasetId(item.id); onSelectDataset(item.id); }}><span><strong>{item.name}</strong><small>{item.row_count.toLocaleString()} rows · {item.manifest_version}</small></span><span>{item.status.replaceAll("_", " ")}</span></button>)}</div></div><div className="workflow-actions"><label className="button secondary upload-button">+ Add dataset<input type="file" accept=".csv,.parquet" onChange={(event) => { const file = event.target.files?.[0]; if (file) onUploadPreview(file.name); event.currentTarget.value = ""; }} /></label><button className="button primary" onClick={() => onStartStep("UPLOAD_PROFILE")} disabled={!canOperate || Boolean(activeJob) || !selectedRuleDataset}>Run Rule Proposer <span aria-hidden="true">→</span></button><small>{selectedRuleDataset ? `Selected: ${selectedRuleDataset.name}` : "Select a dataset to enable the run."}</small>{!canOperate && <small>Steward access is required to start a workflow.</small>}</div></section></div></section>;
  }
  if (!dataset) {
    return <div className="empty-state"><span className="eyebrow">WORKFLOW</span><h2>Select a dataset to begin.</h2><p className="muted">The workflow will keep every agent artifact scoped to the selected dataset.</p></div>;
  }
  const currentStep = workflow.steps.find((step) => step.key === workflow.current_step);
  const currentArtifact = currentStep ? workflowArtifactForStep(workflow, artifacts, currentStep.key) : undefined;
  const payload = currentArtifact?.payload && typeof currentArtifact.payload === "object" ? currentArtifact.payload as Record<string, unknown> : null;
  const isRunning = Boolean(activeJob) || currentStep?.status === "RUNNING";
  const canRun = canOperate && currentStep?.status === "READY" && !isRunning;
  const reviewable = currentArtifact && currentStep && ["REVIEW_RULES", "REVIEW_EXECUTE", "ANALYZE_IMPROVE"].includes(currentStep.key) && ["WAITING_APPROVAL", "READY"].includes(currentStep.status) && ["DRAFT", "VALIDATED", "APPROVED"].includes(currentArtifact.status);
  const rulesDecided = proposals.length > 0 && proposals.some((proposal) => proposal.status === "APPROVED") && proposals.every((proposal) => ["APPROVED", "REJECTED"].includes(proposal.status));
  const renderArtifact = () => {
    if (!currentArtifact || !payload) return <div className="workflow-artifact-empty">This step has not produced an artifact yet.</div>;
    if (currentArtifact.type === "SEMANTIC_CONTRACT") {
      const contractColumns = Array.isArray(payload.columns)
        ? payload.columns.filter((column): column is Record<string, unknown> => Boolean(column && typeof column === "object"))
        : [];
      return <div className="understanding-holder">
        <div className="understanding-summary"><span className="eyebrow">DATA UNDERSTANDING</span><p>{String(payload.summary ?? "Agent 1 has not supplied a summary yet.")}</p></div>
        <div className="understanding-meta">
          <div><span>Rows</span><strong>{(profile?.row_count ?? dataset.row_count).toLocaleString()}</strong></div>
          <div><span>Columns</span><strong>{(profile?.columns.length ?? contractColumns.length).toLocaleString()}</strong></div>
          <div><span>Completeness</span><strong>{profile ? `${profile.completeness_score.toFixed(1)}%` : "—"}</strong></div>
          <div><span>Validity</span><strong>{profile ? `${profile.validity_score.toFixed(1)}%` : "—"}</strong></div>
          <div><span>Source</span><strong>{dataset.source_label}</strong></div>
          <div><span>Manifest</span><strong>{dataset.manifest_version}</strong></div>
        </div>
        <div className="understanding-section"><div className="panel-heading"><div><span className="eyebrow">INFERRED SCHEMA</span><h3>Semantic columns</h3></div><span className="muted">{contractColumns.length} mapped</span></div><div className="schema-list">{contractColumns.map((column) => <div className="schema-row" key={String(column.name)}><strong>{String(column.name ?? "Unnamed column")}</strong><span>{String(column.semantic_type ?? "unknown")}</span><small>{typeof column.confidence === "number" ? `${Math.round(column.confidence * 100)}% confidence` : "No confidence score"}</small></div>)}</div></div>
        <div className="understanding-section"><div className="panel-heading"><div><span className="eyebrow">PROFILE EVIDENCE</span><h3>Signals used by Agent 1</h3></div></div><div className="evidence-list">{Array.isArray(payload.evidence) && payload.evidence.map((evidence) => <span key={String(evidence)} className="evidence-chip">{String(evidence)}</span>)}</div></div>
      </div>;
    }
    if (currentArtifact.type === "CODE_PROPOSAL") return <><div className="artifact-code"><code>{`-- ${String(payload.target ?? "standardized_dataset")}`}</code><code>select * from source_dataset</code><code>-- normalize timestamps to UTC</code><code>-- trim controlled categorical values</code></div><div className="artifact-meta"><span>Deterministic: {String((payload.validation as Record<string, unknown> | undefined)?.deterministic ?? true)}</span><span>Destructive: {String((payload.validation as Record<string, unknown> | undefined)?.destructive ?? false)}</span></div></>;
    if (currentArtifact.type === "LOOP_RECOMMENDATION") return <><p className="hypothesis">{String(payload.hypothesis ?? "No hypothesis supplied.")}</p><div className="evidence-list">{Array.isArray(payload.supporting_signals) && payload.supporting_signals.map((signal) => <span key={String(signal)} className="evidence-chip">{String(signal)}</span>)}</div><p className="muted">Next action: {String(payload.next_action ?? "Review the latest run.")}</p></>;
    return <><p>{String(payload.summary ?? `${currentArtifact.type.replaceAll("_", " ")} generated by ${currentArtifact.agent_role}.`)}</p><div className="artifact-meta"><span>Version {currentArtifact.version}</span><span>{currentArtifact.status}</span>{Array.isArray(payload.evidence) && <span>{payload.evidence.length} evidence references</span>}{typeof payload.proposal_count === "number" && <span>{payload.proposal_count} typed rules</span>}</div></>;
  };
  const nextActionLabel = currentStep?.key === "UPLOAD_PROFILE" ? "Prepare dataset" : currentStep?.key === "UNDERSTAND_DATA" ? "Run Agent 1 understanding" : currentStep?.key === "PROPOSE_RULES" ? "Generate rule proposals" : currentStep?.key === "PROPOSE_CODE" ? "Generate standardization code" : "Run current step";
  const currentStepIndex = currentStep ? workflow.steps.findIndex((step) => step.key === currentStep.key) : -1;
  const nextWorkflowStep = currentStepIndex >= 0 ? workflow.steps[currentStepIndex + 1] : undefined;
  const canAdvance = Boolean(currentStep && currentStep.status === "COMPLETED" && !currentStep.temporary && nextWorkflowStep?.status === "READY" && canOperate && !activeJob);
  const visibleWorkflowSteps = workflow.steps;
  const previousWorkflowStep = currentStepIndex > 0 ? visibleWorkflowSteps[currentStepIndex - 1] : undefined;
  const canMoveBackward = Boolean(previousWorkflowStep && canOperate && !activeJob);
  const canMoveForward = canAdvance;
  return <div className="workflow-page"><div className="page-heading"><div><span className="eyebrow">WORKFLOW RUN {workflow.id}</span><h1>Dataset to decision</h1><p>{dataset.name} · iteration {workflow.iteration} of {workflow.max_iterations}</p></div><div className="page-heading-actions"><button type="button" className="step-nav-button backward" onClick={() => previousWorkflowStep && onRewindStep(previousWorkflowStep.key)} disabled={!canMoveBackward}>← Back</button><button type="button" className="step-nav-button forward" onClick={onAdvanceStep} disabled={!canMoveForward}>Forward →</button></div></div><div className="workflow-layout"><aside className="workflow-stepper" aria-label="Workflow steps">{visibleWorkflowSteps.map((step, index) => { const meta = workflowStepLabels[step.key]; const statusLabel = step.temporary ? "TEMPORARY SESSION" : step.status === "LOCKED" ? "" : step.status.replaceAll("_", " "); return <button type="button" disabled className={`workflow-step ${step.key === workflow.current_step ? "current" : ""} ${step.status.toLowerCase()} ${step.temporary ? "temporary" : ""}`} key={step.key} aria-label={meta.label}><div className="workflow-step-index">{step.status === "COMPLETED" && !step.temporary ? "✓" : index + 1}</div><div className="workflow-step-copy"><strong>{meta.label}</strong><span>{meta.owner}</span>{statusLabel && <small>{statusLabel}</small>}{step.blocker && <em>{step.blocker}</em>}</div></button>; })}</aside><section className="workflow-detail panel"><div className="workflow-detail-heading"><div><span className="eyebrow">CURRENT STEP</span><h2>{currentStep ? workflowStepLabels[currentStep.key].label : "Complete"}</h2><p className="muted">{currentStep ? workflowStepLabels[currentStep.key].description : "The workflow is complete."}</p></div>{currentStep && <StatusPill label={currentStep.temporary ? "TEMPORARY SESSION" : currentStep.status.replaceAll("_", " ")} tone={currentStep.temporary ? "info" : currentStep.status === "FAILED" ? "danger" : currentStep.status === "WAITING_APPROVAL" ? "warning" : currentStep.status === "COMPLETED" ? "success" : "info"} />}</div>{activeJob && <ProgressPanel job={activeJob} title={`Running ${workflowStepLabels[workflow.current_step].label}`} />}<div className="workflow-artifact"><div className="panel-heading"><div><span className="eyebrow">AGENT ARTIFACT</span><h3>{currentArtifact ? currentArtifact.type.replaceAll("_", " ") : "Waiting for output"}</h3></div>{currentArtifact && <StatusPill label={currentArtifact.status} tone={currentArtifact.status === "APPROVED" ? "success" : currentArtifact.status === "REJECTED" ? "danger" : "info"} />}</div>{renderArtifact()}</div>{currentStep?.key === "REVIEW_RULES" && <RulesPage proposals={proposals} configurations={configurations} profileReady busy={Boolean(activeJob)} canOperate={canOperate && !currentStep.temporary} onRequestProposals={() => undefined} onApprove={onApproveRule} onReject={onRejectRule} onEdit={onEditRule} onDelete={onDeleteRule} onSaveConfiguration={onSaveConfiguration} onCreateManual={onCreateManualRule} onRun={() => undefined} pipelineMode /> }<div className="workflow-actions">{currentStep && ["READY", "FAILED"].includes(currentStep.status) && <button className="button primary" disabled={!canRun || (currentStep.key === "REVIEW_RULES" && Boolean(currentArtifact))} onClick={() => onStartStep(currentStep.key)}>{nextActionLabel}</button>}{currentStep?.temporary && <span className="muted">Viewing a preserved session. Use Back and Forward to navigate between stages.</span>}{reviewable && !currentStep?.temporary && <><button className="button primary" disabled={!canOperate || (currentStep?.key === "REVIEW_RULES" && !rulesDecided)} onClick={() => onReviewArtifact(currentArtifact.id, { action: "approve" })}>Confirm stage and continue</button>{currentStep?.key === "REVIEW_RULES" && !rulesDecided && <span className="muted">Decide every rule and keep at least one approved rule before continuing.</span>}<button className="button ghost" disabled={!canOperate} onClick={() => onReviewArtifact(currentArtifact.id, { action: "request_revision" })}>Request revision</button><button className="button danger" disabled={!canOperate} onClick={() => onReviewArtifact(currentArtifact.id, { action: "reject" })}>Reject artifact</button></>}{currentStep?.key === "ANALYZE_IMPROVE" && currentStep.status === "WAITING_APPROVAL" && <><button className="button primary" disabled={!canOperate} onClick={() => onLoopDecision({ action: "continue" })}>Continue loop</button><button className="button ghost" disabled={!canOperate} onClick={() => onLoopDecision({ action: "stop" })}>Stop loop</button></>}{!canOperate && <span className="muted">Read-only role: review is disabled.</span>}</div></section></div></div>;
}

function App() {
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
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(
    () => sessionStorage.getItem("ridepulse.dataset") ?? null,
  );
  const [profile, setProfile] = useState<DatasetProfile | null>(null);
  const [datasetProfiles, setDatasetProfiles] = useState<Record<string, DatasetProfile>>({});
  const [proposals, setProposals] = useState<RuleProposal[]>([]);
  const [ruleConfigurations, setRuleConfigurations] = useState<RuleConfiguration[]>([]);
  const [adminUsers, setAdminUsers] = useState<UserAccount[]>([]);
  const [datasetAccess, setDatasetAccess] = useState<DatasetAccess[]>([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [activeRun, setActiveRun] = useState<DqRun | null>(null);
  const [dqResults, setDqResults] = useState<DqResult[]>([]);
  const [dqAnomalies, setDqAnomalies] = useState<DqAnomaly[]>([]);
  const [qualityTrends, setQualityTrends] = useState<QualityTrendPoint[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowRun | null>(null);
  const [workflowArtifacts, setWorkflowArtifacts] = useState<AgentArtifact[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [retryAction, setRetryAction] = useState<(() => void) | null>(null);
  const [editingProposal, setEditingProposal] = useState<RuleProposal | null>(
    null,
  );
  const [manualRuleOpen, setManualRuleOpen] = useState(false);

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
      const profileEntries = await Promise.all(nextDatasets.filter((item) => item.status === "PROFILE_READY").map(async (item) => [item.id, await api.getProfile(item.id)] as const));
      const nextProfiles = Object.fromEntries(profileEntries.filter((entry): entry is [string, DatasetProfile] => Boolean(entry[1]))) as Record<string, DatasetProfile>;
      setDatasetProfiles(nextProfiles);
      const rememberedDatasetId = sessionStorage.getItem("ridepulse.dataset");
      const nextDataset = nextDatasets.find((item) => item.id === rememberedDatasetId) ?? nextDatasets[0];
      setSelectedDatasetId(nextDataset?.id ?? null);
      if (nextDataset) sessionStorage.setItem("ridepulse.dataset", nextDataset.id);
      if (nextDataset?.status === "PROFILE_READY") {
        const [nextProposals, nextConfigurations, latestRun, nextTrends] = await Promise.all([
          api.listProposals(nextDataset.id),
          api.listRuleConfigurations(nextDataset.id),
          api.getLatestDqRun(nextDataset.id),
          api.getQualityTrends(nextDataset.id),
        ]);
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
      } else {
        setProfile(null);
        setProposals([]);
        setRuleConfigurations([]);
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

  const refreshAdmin = useCallback(async () => {
    if (!dataset || !canAdmin) return;
    setAdminLoading(true);
    try {
      const [users, access] = await Promise.all([api.listUsers(), api.listDatasetAccess(dataset.id)]);
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
  }

  useEffect(() => {
    if (authenticated) void refreshWorkspace();
  }, [authenticated, refreshWorkspace]);
  useEffect(() => { if (view === "admin") void refreshAdmin(); }, [refreshAdmin, view]);
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
      attempt < 30 &&
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
      setToast("Job completed successfully.");
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
        if (nextProfile) setDatasetProfiles((current) => ({ ...current, [dataset.id]: nextProfile }));
      });
    } catch (err) {
      setError(getErrorMessage(err, "Unable to start analysis."));
    }
  }

  async function requestProposals() {
    if (!dataset) return;
    setError("");
    setRetryAction(null);
    try {
      const job = await api.startRuleProposals(dataset.id, crypto.randomUUID());
      await pollJob(job, async () => {
        setProposals(await api.listProposals(dataset.id));
        setAuditLogs(await api.listAuditLogs());
        setView("rules");
      });
    } catch (err) {
      setError(getErrorMessage(err, "Unable to request proposals."));
    }
  }

  async function reviewProposal(id: string, action: "approve" | "reject") {
    try {
      await api.reviewProposal(id, { action });
      setProposals(await api.listProposals(dataset.id));
      setRuleConfigurations(await api.listRuleConfigurations(dataset.id));
      setAuditLogs(await api.listAuditLogs());
      setToast(
        action === "approve"
          ? "Rule approved for execution."
          : "Proposal rejected and kept out of execution.",
      );
    } catch (err) {
      setError(getErrorMessage(err, "Unable to update proposal."));
    }
  }

  async function deleteProposal(id: string) {
    if (!dataset) return;
    try {
      await api.deleteProposal(id);
      setProposals(await api.listProposals(dataset.id));
      setRuleConfigurations(await api.listRuleConfigurations(dataset.id));
      setAuditLogs(await api.listAuditLogs());
      setToast("Proposal removed. Audit history was retained.");
    } catch (err) { setError(getErrorMessage(err, "Unable to delete proposal.")); }
  }

  async function saveRuleConfiguration(id: string, input: RuleConfigurationInput) {
    if (!dataset) return;
    try {
      await api.updateRuleConfiguration(id, input);
      setRuleConfigurations(await api.listRuleConfigurations(dataset.id));
      setAuditLogs(await api.listAuditLogs());
      setToast("Execution settings saved.");
    } catch (err) { setError(getErrorMessage(err, "Unable to update rule settings.")); }
  }

  async function createAdminUser(input: UserCreateInput) {
    try { await api.createUser(input); await refreshAdmin(); setToast(`Account '${input.username}' created.`); }
    catch (err) { setError(getErrorMessage(err, "Unable to create account.")); }
  }

  async function updateAdminUser(username: string, input: UserUpdateInput) {
    try { await api.updateUser(username, input); await refreshAdmin(); setToast(`Account '${username}' updated.`); }
    catch (err) { setError(getErrorMessage(err, "Unable to update account.")); }
  }

  async function grantAdminAccess(username: string, accessLevel: DatasetAccessLevel) {
    if (!dataset) return;
    try { await api.grantDatasetAccess(dataset.id, username, accessLevel); await refreshAdmin(); setToast(`Dataset access updated for '${username}'.`); }
    catch (err) { setError(getErrorMessage(err, "Unable to grant dataset access.")); }
  }

  async function revokeAdminAccess(username: string) {
    if (!dataset) return;
    try { await api.revokeDatasetAccess(dataset.id, username); await refreshAdmin(); setToast(`Dataset access revoked for '${username}'.`); }
    catch (err) { setError(getErrorMessage(err, "Unable to revoke dataset access.")); }
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
      setProposals(await api.listProposals(dataset.id));
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
      setProposals(await api.listProposals(dataset.id));
      setAuditLogs(await api.listAuditLogs());
      setManualRuleOpen(false);
      setToast("Manual rule created and queued for approval.");
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

  async function startWorkflowStep(step: WorkflowStepKey) {
    if (!dataset || !canOperate) return;
    setError("");
    setRetryAction(null);
    try {
      let currentWorkflow = workflow;
      if (!currentWorkflow) {
        currentWorkflow = await workflowApi.createWorkflow(dataset.id);
        setWorkflow(currentWorkflow);
        setWorkflowArtifacts(await workflowApi.listWorkflowArtifacts(currentWorkflow.id));
      }
      const queuedJob = await workflowApi.runWorkflowStep(currentWorkflow.id, step);
      await pollJob(queuedJob, async () => {
        await refreshWorkflow(currentWorkflow!.id);
        setProfile(await api.getProfile(dataset.id));
        setProposals(await api.listProposals(dataset.id));
        setAuditLogs(await api.listAuditLogs());
      }, workflowApi);
    } catch (err) {
      setError(getErrorMessage(err, "Unable to run workflow step."));
    }
  }

  async function advanceWorkflowStep() {
    if (!workflow || !canOperate || activeJob) return;
    try {
      const nextWorkflow = await workflowApi.advanceWorkflowStep(workflow.id);
      setWorkflow(nextWorkflow);
      setToast(`Moved to ${workflowStepLabels[nextWorkflow.current_step].label}.`);
    } catch (err) {
      setError(getErrorMessage(err, "Unable to move to the next workflow step."));
    }
  }

  async function reviewWorkflowArtifact(id: string, input: ArtifactReviewInput) {
    if (!canOperate) return;
    try {
      const updated = await workflowApi.reviewArtifact(id, input);
      setWorkflowArtifacts((current) => current.map((artifact) => artifact.id === id ? updated : artifact));
      if (workflow) await refreshWorkflow(workflow.id);
      setToast(input.action === "approve" ? "Artifact approved. The next workflow step is ready." : input.action === "reject" ? "Artifact rejected and kept out of execution." : "Revision requested from the agent.");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to review workflow artifact."));
    }
  }

  async function decideWorkflowLoop(input: LoopDecisionInput) {
    if (!workflow || !canOperate) return;
    try {
      setWorkflow(await workflowApi.continueLoop(workflow.id, input));
      setWorkflowArtifacts(await workflowApi.listWorkflowArtifacts(workflow.id));
      setAuditLogs(await api.listAuditLogs());
      setToast(input.action === "continue" ? "Loop continued with a bounded next iteration." : "Loop stopped by the steward.");
    } catch (err) {
      setError(getErrorMessage(err, "Unable to update loop decision."));
    }
  }

  async function rewindWorkflowStage(targetStep: WorkflowStepKey) {
    if (!workflow || !canOperate || activeJob) return;
    const label = workflowStepLabels[targetStep].label;
    try {
      const nextWorkflow = await workflowApi.rewindWorkflow(workflow.id, targetStep);
      setWorkflow(nextWorkflow);
      setWorkflowArtifacts(await workflowApi.listWorkflowArtifacts(workflow.id));
      setToast(`Returned to ${label}. Later stage sessions are kept temporarily until this stage changes.`);
    } catch (err) {
      setError(getErrorMessage(err, "Unable to return to workflow stage."));
    }
  }

  if (!authenticated)
    return (
      <LoginScreen onLogin={handleLogin} busy={loginBusy} error={loginError} />
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-mark">RP</span>
          <span>
            RidePulse <em>DQ</em>
          </span>
        </div>
        <div className="sidebar-label">WORKSPACE</div>
        <nav>
          {(["overview", "workflow", "datasets", "audit", ...(canAdmin ? ["admin" as View] : [])] as View[]).map((item) => (
            <button
              key={item}
              className={`nav-item ${view === item ? "active" : ""}`}
              onClick={() => setView(item)}
            >
              <span className="nav-icon">
                {item === "overview"
                  ? "◈"
                  : item === "workflow"
                    ? "↯"
                  : item === "datasets"
                    ? "▦"
                  : item === "rules"
                    ? "✦"
                    : item === "runs"
                    ? "↗"
                    : item === "visualization"
                      ? "⌁"
                      : item === "data"
                        ? "▦"
                      : item === "admin" ? "⚙" : "≡"}
              </span>
              {item === "overview"
                ? "Overview"
                : item === "workflow"
                  ? "Rule proposer"
                : item === "datasets"
                  ? "Datasets"
                : item === "rules"
                  ? "Rule proposals"
                  : item === "runs"
                    ? "DQ runs"
                    : item === "visualization"
                      ? "Visualizations"
                      : item === "data"
                        ? "Data explorer"
                    : item === "admin" ? "Admin control" : "Audit history"}
              {item === "rules" &&
                proposals.some((proposal) =>
                  ["PROPOSED", "EDITED"].includes(proposal.status),
                ) && (
                  <span className="nav-count">
                    {
                      proposals.filter((proposal) =>
                        ["PROPOSED", "EDITED"].includes(proposal.status),
                      ).length
                    }
                  </span>
                )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="security-card">
            <span className="shield">✦</span>
            <div>
              <strong>Guardrails active</strong>
              <small>Aggregate evidence only</small>
            </div>
          </div>
          <button
            className="profile-button"
            onClick={() => void handleLogout()}
          >
            <span className="avatar">
              {role === "ADMIN" ? "AD" : role === "STEWARD" ? "DS" : "US"}
            </span>
            <span>
              <strong>{username || role}</strong>
              <small>{role} · Sign out</small>
            </span>
            <span className="chevron">↗</span>
          </button>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span>
            <span>/</span>
            <strong>
              {view === "overview"
                ? "Overview"
                : view === "workflow"
                  ? "Rule proposer"
                : view === "datasets"
                  ? "Datasets"
                : view === "rules"
                  ? "Rule proposals"
                  : view === "runs"
                    ? "DQ runs"
                    : view === "visualization"
                      ? "Visualizations"
                      : view === "data"
                        ? "Data explorer"
                    : view === "admin" ? "Admin control" : "Audit history"}
            </strong>
          </div>
          <div className="topbar-actions">
            <span className={`mode-badge ${isMockMode ? "mock" : "live"}`}>
              <span />
              {isMockMode ? "LOCAL MOCK ADAPTER" : "CONNECTED API"}
            </span>
            <span className="role-badge">{role}</span>
            <button className="icon-button" aria-label="Notifications">
              ♢
            </button>
            <ThemeControl />
            <button className="avatar mini" aria-label="Current user">
              {role === "ADMIN" ? "AD" : role === "STEWARD" ? "DS" : "US"}
            </button>
          </div>
        </header>
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
              <strong>{getErrorMessage(error, "Action needs attention") === "Action needs attention" ? "Action needs attention" : "Action failed"}</strong>
              <span>{getErrorMessage(error, "Action needs attention") === "Action needs attention" ? "Retry the current step or open the audit log for details." : getErrorMessage(error, "Action needs attention")}</span>
              <button onClick={() => setError("")}>×</button>
            </div>
          )}
          {toast && (
            <div className="alert success">
              <strong>Done</strong>
              <span>{toast}</span>
              <button onClick={() => setToast("")}>×</button>
            </div>
          )}
          {activeJob && (
            <ProgressPanel
              job={activeJob}
              title={
                activeJob.type === "INGEST_PROFILE"
                  ? "Building dataset profile"
                  : activeJob.type === "PROPOSE_RULES"
                    ? "Generating rule proposals"
                    : "Running approved checks"
              }
            />
          )}
          {view === "overview" && (
            <OverviewPage
              dataset={dataset}
              datasets={datasets}
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
              onNavigate={setView}
            />
          )}
          {view === "workflow" && (
            <WorkflowPage
              dataset={dataset}
              profile={profile}
              datasets={datasets}
              workflow={workflow}
              artifacts={workflowArtifacts}
              proposals={proposals}
              configurations={ruleConfigurations}
              activeJob={activeJob}
              canOperate={canOperate}
              onStartStep={(step) => void startWorkflowStep(step)}
              onAdvanceStep={() => void advanceWorkflowStep()}
              onReviewArtifact={(id, input) => void reviewWorkflowArtifact(id, input)}
              onLoopDecision={(input) => void decideWorkflowLoop(input)}
              onApproveRule={(id) => void reviewProposal(id, "approve")}
              onRejectRule={(id) => void reviewProposal(id, "reject")}
              onEditRule={setEditingProposal}
              onDeleteRule={(id) => void deleteProposal(id)}
              onSaveConfiguration={(id, input) => void saveRuleConfiguration(id, input)}
              onCreateManualRule={() => setManualRuleOpen(true)}
              onRewindStep={(step) => void rewindWorkflowStage(step)}
              onSelectDataset={(id) => void selectDataset(id)}
              onUploadPreview={(fileName) => setToast(`Selected ${fileName}. Upload is ready for the backend contract; this preview keeps the catalog unchanged.`)}
              onBackToDatasetSelection={() => { setWorkflow(null); setView("workflow"); }}
            />
          )}
          {view === "datasets" && (
            <DatasetsPage
              datasets={datasets}
              dataset={dataset}
              onOpenExplorer={(datasetId) => { if (datasetId !== dataset?.id) void selectDataset(datasetId); setView("data"); }}
            />
          )}
          {view === "rules" && (
            <RulesPage
              proposals={proposals}
              profileReady={Boolean(profile)}
              busy={Boolean(activeJob)}
              canOperate={canOperate}
              onRequestProposals={() => void requestProposals()}
              onApprove={(id) => void reviewProposal(id, "approve")}
              onReject={(id) => void reviewProposal(id, "reject")}
              onEdit={setEditingProposal}
              configurations={ruleConfigurations}
              onDelete={(id) => void deleteProposal(id)}
              onSaveConfiguration={(id, input) => void saveRuleConfiguration(id, input)}
              onCreateManual={() => setManualRuleOpen(true)}
              onRun={() => void runApprovedRules()}
            />
          )}
          {view === "runs" && (
            <RunsPage
              activeRun={activeRun}
              results={dqResults}
              anomalies={dqAnomalies}
              approvedCount={approvedRules.length}
              busy={Boolean(activeJob)}
              canOperate={canOperate}
              onRun={() => void runApprovedRules()}
            />
          )}
          {view === "visualization" && (
            <VisualizationPage
              profile={profile}
              results={dqResults}
              anomalies={dqAnomalies}
              trends={qualityTrends}
            />
          )}
          {view === "data" && <DataExplorerPage dataset={dataset} />}
          {view === "audit" && <AuditPage logs={auditLogs} />}
          {view === "admin" && canAdmin && <AdminPage users={adminUsers} access={datasetAccess} loading={adminLoading} onCreate={createAdminUser} onUpdate={updateAdminUser} onGrant={grantAdminAccess} onRevoke={revokeAdminAccess} />}
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
}) {
  const proposalCount = proposals.filter((proposal) => ["PROPOSED", "EDITED"].includes(proposal.status)).length;
  const qualityRows = datasets.map((item) => {
    const itemProfile = datasetProfiles[item.id] ?? (item.id === dataset?.id ? profile : null);
    const score = itemProfile ? (itemProfile.completeness_score + itemProfile.validity_score) / 2 : null;
    return { dataset: item, profile: itemProfile, score };
  });
  const profiledRows = qualityRows.filter((row) => row.score !== null);
  const averageQuality = profiledRows.length ? profiledRows.reduce((sum, row) => sum + (row.score ?? 0), 0) / profiledRows.length : null;
  const totalRows = datasets.reduce((sum, item) => sum + item.row_count, 0);
  const profileReadyCount = datasets.filter((item) => item.status === "PROFILE_READY").length;
  const statusRows = [
    { label: "Profile ready", count: datasets.filter((item) => item.status === "PROFILE_READY").length },
    { label: "Ingested", count: datasets.filter((item) => item.status === "INGESTED").length },
    { label: "Registered", count: datasets.filter((item) => item.status === "REGISTERED").length },
  ];
  const statusMax = Math.max(1, ...statusRows.map((row) => row.count));
  if (!dataset)
    return (
      <>
        <div className="page-heading"><div><span className="eyebrow">QUALITY COMMAND CENTER</span><h1>No registered dataset</h1><p>The backend has not registered a Gate 2 dataset yet.</p></div></div>
        <section className="empty-state"><div className="empty-illustration">▦</div><h2>Dataset catalog is empty</h2><p>Upload or register a dataset to populate the multi-dataset quality dashboard.</p></section>
      </>
    );
  return (
    <>
      <div className="page-heading overview-heading">
        <div><span className="eyebrow">QUALITY COMMAND CENTER</span><h1>Dataset quality overview</h1><p>Compare quality signals across the catalog before opening an individual pipeline.</p></div>
        <div className="heading-actions"><button className="button ghost" onClick={() => onNavigate("datasets")}>Dataset catalog →</button><button className="button primary" onClick={() => onNavigate("visualization")}>Open observatory →</button></div>
      </div>
      <section className="stat-grid overview-kpis">
        <StatCard label="Datasets" value={`${datasets.length}`} detail="Registered in workspace" tone="green" />
        <StatCard label="Profile ready" value={`${profileReadyCount}/${datasets.length}`} detail="Datasets with aggregate profile" tone="blue" />
        <StatCard label="Rows tracked" value={totalRows.toLocaleString()} detail="Across registered datasets" tone="amber" />
        <StatCard label="Average quality" value={averageQuality === null ? "—" : `${averageQuality.toFixed(1)}%`} detail={profiledRows.length ? `${profiledRows.length} profiled dataset${profiledRows.length === 1 ? "" : "s"}` : "Awaiting profile data"} tone="violet" />
      </section>
      <section className="overview-grid">
        <article className="panel overview-dataset-panel"><div className="panel-heading"><div><span className="eyebrow">CATALOG QUALITY MAP</span><h3>Quality by dataset</h3></div><span className="panel-caption">{datasets.length} registered</span></div><div className="overview-dataset-list">{qualityRows.map((row) => <div className={`overview-dataset-row ${row.dataset.id === dataset.id ? "active" : ""}`} key={row.dataset.id}><div className="overview-dataset-id"><span className="dataset-mini-icon">⌁</span><div><strong>{row.dataset.name}</strong><small>{row.dataset.source_label} · {row.dataset.row_count.toLocaleString()} rows</small></div></div><StatusPill label={row.dataset.status.replaceAll("_", " ")} tone={row.dataset.status === "PROFILE_READY" ? "success" : "info"} /><div className="overview-dataset-score">{row.score === null ? <span className="muted">Profile pending</span> : <><div className="overview-score-track"><span style={{ width: `${row.score}%` }} /></div><strong>{row.score.toFixed(1)}%</strong></>}</div></div>)}</div></article>
        <article className="panel overview-status-panel"><div className="panel-heading"><div><span className="eyebrow">CATALOG STATUS</span><h3>Readiness distribution</h3></div><span className="panel-caption">{approvedRules} approved rules active</span></div><div className="overview-status-list">{statusRows.map((row) => <div className="overview-status-row" key={row.label}><div><span>{row.label}</span><strong>{row.count}</strong></div><div className="overview-status-track"><span style={{ width: `${(row.count / statusMax) * 100}%` }} /></div></div>)}</div><div className="overview-status-footer"><span>Review queue</span><strong>{proposalCount} pending</strong></div></article>
      </section>
      <section className="overview-chart-grid">
        <article className="panel overview-trend-panel"><div className="panel-heading"><div><span className="eyebrow">ACTIVE DATASET TREND</span><h3>Quality score over time</h3></div><button className="text-button" onClick={() => onNavigate("visualization")}>Open full view →</button></div><TrendChart points={qualityTrends} /></article>
        <article className="panel overview-compare-panel"><div className="panel-heading"><div><span className="eyebrow">QUALITY COMPARISON</span><h3>Completeness vs validity</h3></div><span className="panel-caption">Profiled datasets only</span></div><OverviewQualityBars rows={qualityRows} /></article>
      </section>
      <section className="overview-action-panel next-panel"><div><span className="eyebrow">NEXT ACTION</span><h3>{profile ? "Continue the active pipeline" : "Build the first profile"}</h3><p>{profile ? "The active dataset is profiled. Move into Rule proposer to review the next agent step." : "Run ingestion and profiling to make this dataset available for cross-dataset comparison."}</p></div><div className="overview-action-buttons">{canOperate && (!profile ? <button className="button secondary" onClick={onStartAnalysis} disabled={loading || busy}>Start profiling →</button> : proposalCount ? <button className="button secondary" onClick={() => onNavigate("rules")}>Open review queue →</button> : <button className="button secondary" onClick={onRequestProposals} disabled={busy}>Generate proposals →</button>)}<button className="button ghost" onClick={() => onNavigate("audit")}>View audit trail</button></div></section>
    </>
  );
}

function OverviewQualityBars({ rows }: { rows: Array<{ dataset: Dataset; profile: DatasetProfile | null; score: number | null }> }) {
  return <div className="overview-quality-bars">{rows.map((row) => <div className="overview-quality-bar" key={row.dataset.id}><div className="overview-quality-label"><strong>{row.dataset.name}</strong><span>{row.score === null ? "Profile pending" : `${row.score.toFixed(1)}% overall`}</span></div><div className="overview-quality-lines"><div><span>Completeness</span><div className="overview-line-track"><i style={{ width: `${row.profile?.completeness_score ?? 0}%` }} /></div><strong>{row.profile ? `${row.profile.completeness_score.toFixed(1)}%` : "—"}</strong></div><div><span>Validity</span><div className="overview-line-track validity"><i style={{ width: `${row.profile?.validity_score ?? 0}%` }} /></div><strong>{row.profile ? `${row.profile.validity_score.toFixed(1)}%` : "—"}</strong></div></div></div>)}</div>;
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
  return (
    <div className={`stat-card ${tone}`}>
      <span className="stat-label">{label}</span>
      <strong>{value}</strong>
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
  const [expandedConfigurationId, setExpandedConfigurationId] = useState<string | null>(null);
  const pending = proposals.filter((proposal) =>
    ["PROPOSED", "EDITED"].includes(proposal.status),
  );
  const approved = proposals.filter(
    (proposal) => proposal.status === "APPROVED",
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{pipelineMode ? "PIPELINE STAGE 4" : "HUMAN-IN-THE-LOOP"}</span>
          <h1>{pipelineMode ? "Review rules before code generation" : "Rule proposals"}</h1>
          <p>{pipelineMode ? "Accept, edit, reject or add a manual rule. Agent 2 stays locked until this set is approved." : "Review agent suggestions or author a typed rule manually."}</p>
        </div>
        <div className="heading-actions">
          {canOperate && (
            <button className="button secondary" onClick={onCreateManual}>
              + Add manual rule
            </button>
          )}
          {!pipelineMode && <button className="button primary" onClick={onRun} disabled={!approved.length || busy || !canOperate}>Run approved rules <span>→</span></button>}
        </div>
      </div>
      {!profileReady ? (
        <section className="empty-state">
          <div className="empty-illustration">✦</div>
          <h2>Profile first, proposals second</h2>
          <p>
            Complete the dataset analysis before asking the guarded Agent for
            proposals.
          </p>
          {canOperate && (
            <button className="button secondary" onClick={onCreateManual}>
              Add manual rule anyway
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
          <div className="review-summary">
            <div>
              <span className="eyebrow">REVIEW QUEUE</span>
              <strong>{pending.length} awaiting decision</strong>
            </div>
            <div className="review-progress">
              <span
                style={{
                  width: `${proposals.length ? ((proposals.length - pending.length) / proposals.length) * 100 : 0}%`,
                }}
              />
            </div>
            <span>
              {approved.length} approved ·{" "}
              {
                proposals.filter((proposal) => proposal.status === "REJECTED")
                  .length
              }{" "}
              rejected
            </span>
          </div>
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
                configuration={configurations.find((item) => item.rule_id === proposal.id)}
                onSaveConfiguration={(input) => onSaveConfiguration(proposal.id, input)}
                configurationExpanded={expandedConfigurationId === proposal.id}
                onToggleConfiguration={() => setExpandedConfigurationId((current) => current === proposal.id ? null : proposal.id)}
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
        <span className="proposal-source">{proposal.source === "MANUAL" ? "Manual rule" : "Agent proposal"}</span>
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
      {(editable || proposal.status === "REJECTED") && canOperate && (
        <div className="proposal-actions">
          {canReject && (
            <button className="button ghost" onClick={onReject}>
              {proposal.status === "APPROVED"
                ? "Reject approved rule"
                : "Reject"}
            </button>
          )}
          <button className="button secondary" onClick={onEdit}>
            {pending
              ? "Edit"
              : proposal.status === "APPROVED"
                ? "Edit approved rule"
                : "Edit rejected rule"}
          </button>
          {canApprove && (
            <button className="button primary" onClick={onApprove}>
              {proposal.status === "REJECTED"
                ? "Re-approve rule"
                : "Approve rule"}{" "}
              <span>→</span>
            </button>
          )}
          {proposal.status !== "APPROVED" && (
            <button className="button ghost" onClick={onDelete}>Delete</button>
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

function RuleConfigurationControl({ configuration, expanded, onToggle, onSave }: { configuration?: RuleConfiguration; expanded: boolean; onToggle: () => void; onSave: (input: RuleConfigurationInput) => void }) {
  const [executionStatus, setExecutionStatus] = useState<RuleConfiguration["execution_status"]>(configuration?.execution_status ?? "ACTIVE");
  const [frequency, setFrequency] = useState<RuleConfiguration["schedule_frequency"]>(configuration?.schedule_frequency ?? "MANUAL");
  const [timezone, setTimezone] = useState(configuration?.timezone ?? "UTC");
  useEffect(() => {
    setExecutionStatus(configuration?.execution_status ?? "ACTIVE");
    setFrequency(configuration?.schedule_frequency ?? "MANUAL");
    setTimezone(configuration?.timezone ?? "UTC");
  }, [configuration]);
  const frequencyLabel = frequency === "MANUAL" ? "Manual only" : frequency === "HOURLY" ? "Hourly" : "Daily";
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
        <span className={`configuration-state ${executionStatus.toLowerCase()}`}><i />{executionStatus === "ACTIVE" ? "Active" : "Paused"}</span>
        <span className="configuration-summary"><strong>Execution settings</strong><small>{frequencyLabel} · {timezone}</small></span>
        <span className="configuration-action">{expanded ? "Hide options" : "Configure"}<i aria-hidden="true">⌄</i></span>
      </button>
      {expanded && (
        <div className="rule-settings" id={panelId}>
          <div className="rule-settings-fields">
            <label>Status<select value={executionStatus} onChange={(event) => setExecutionStatus(event.target.value as RuleConfiguration["execution_status"])}><option value="ACTIVE">Active</option><option value="PAUSED">Paused</option></select></label>
            <label>Schedule<select value={frequency} onChange={(event) => setFrequency(event.target.value as RuleConfiguration["schedule_frequency"])}><option value="MANUAL">Manual only</option><option value="HOURLY">Hourly</option><option value="DAILY">Daily</option></select></label>
            <label>Timezone<input value={timezone} onChange={(event) => setTimezone(event.target.value)} aria-label="Timezone" /></label>
            <button className="button ghost" onClick={() => onSave({ execution_status: executionStatus, schedule_frequency: frequency, timezone })}>Save settings</button>
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
        <button
          className="button primary"
          onClick={onRun}
          disabled={!approvedCount || busy || !canOperate}
        >
          Run approved rules <span>→</span>
        </button>
      </div>
      {!activeRun ? (
        <section className="empty-state">
          <div className="empty-illustration">↗</div>
          <h2>No run yet</h2>
          <p>
            Approve at least one proposal, then execute it through the read-only
            runner.
          </p>
          {canOperate && (
            <button
              className="button primary"
              onClick={onRun}
              disabled={!approvedCount || busy}
            >
              Run approved rules →
            </button>
          )}
        </section>
      ) : (
        <>
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
            <section className={`panel anomaly-panel ${anomalies.length ? "has-anomalies" : "is-clear"}`}>
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">ANOMALY DETECTION</span>
                  <h3>{anomalies.length ? "Signals requiring attention" : "No anomalous shifts detected"}</h3>
                </div>
                <StatusPill
                  label={anomalies.length ? `${anomalies.length} detected` : "CLEAR"}
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
                    <article className="anomaly-card" key={`${anomaly.rule_id}-${anomaly.anomaly_type}`}>
                      <div className="anomaly-card-top">
                        <strong>{anomaly.rule_title}</strong>
                        <span>{anomaly.anomaly_type === "Z_SCORE_SPIKE" ? "Historical spike" : "High failure rate"}</span>
                      </div>
                      <div className="anomaly-metrics">
                        <div><small>CURRENT</small><strong>{(anomaly.current_rate * 100).toFixed(2)}%</strong></div>
                        <div><small>BASELINE</small><strong>{anomaly.historical_mean == null ? "Cold start" : `${(anomaly.historical_mean * 100).toFixed(2)}%`}</strong></div>
                        <div><small>Z-SCORE</small><strong>{anomaly.z_score == null ? "—" : anomaly.z_score.toFixed(2)}</strong></div>
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
    return <div className="chart-empty">Run approved rules to establish the first quality trend.</div>;
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
    x: points.length === 1 ? width / 2 : insetLeft + (index / (points.length - 1)) * (width - insetLeft - insetRight),
    y: height - insetBottom - ((point.quality_score - minimum) / range) * (height - insetTop - insetBottom),
    point,
  }));
  const line = coordinates.map(({ x, y }) => `${x},${y}`).join(" ");
  const areaPath = coordinates.length > 1
    ? `M ${coordinates[0].x} ${height - insetBottom} L ${coordinates.map(({ x, y }) => `${x} ${y}`).join(" L ")} L ${coordinates.at(-1)!.x} ${height - insetBottom} Z`
    : "";
  const dateLabel = (value: string) => new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
  }).format(new Date(value));
  return (
    <div className="trend-chart-wrap">
      <svg className="trend-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Quality score trend across completed DQ runs">
        <defs>
          <linearGradient id="quality-area" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.2" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 1, 2, 3].map((lineIndex) => {
          const y = insetTop + (lineIndex / 3) * (height - insetTop - insetBottom);
          const value = maximum - (lineIndex / 3) * range;
          return (
            <g key={lineIndex}>
              <line x1={insetLeft} y1={y} x2={width - insetRight} y2={y} className="chart-grid-line" />
              <text x={insetLeft - 10} y={y + 4} className="chart-tick" textAnchor="end">{value.toFixed(0)}%</text>
            </g>
          );
        })}
        {areaPath && <path d={areaPath} className="chart-area" />}
        {coordinates.length > 1 && <polyline points={line} className="chart-line" />}
        {coordinates.map(({ x, y, point }) => (
          <g key={point.run_id}>
            <circle cx={x} cy={y} r="12" className="chart-point-halo" />
            <circle cx={x} cy={y} r="5" className="chart-point" />
            {coordinates.length === 1 && <text x={x} y={y - 22} className="chart-value" textAnchor="middle">{point.quality_score.toFixed(2)}%</text>}
            <title>{`${point.quality_score.toFixed(2)}% · ${new Date(point.created_at).toLocaleString()}`}</title>
          </g>
        ))}
        <text x={insetLeft} y={height - 12} className="chart-date">{dateLabel(points[0].created_at)}</text>
        {points.length > 1 && <text x={width - insetRight} y={height - 12} className="chart-date" textAnchor="end">{dateLabel(points.at(-1)!.created_at)}</text>}
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
  const detectionMode = anomalies[0]?.detection_mode === "HISTORICAL" || (!anomalies.length && historicalReady)
    ? "Historical baseline"
    : "Cold-start screen";
  return (
    <article className="panel anomaly-monitor">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">AUTOMATED ANOMALY DETECTION</span>
          <h3>Violation-rate monitoring</h3>
        </div>
        <StatusPill label={anomalies.length ? `${anomalies.length} SIGNAL${anomalies.length === 1 ? "" : "S"}` : "NO SIGNALS"} tone={anomalies.length ? "warning" : "success"} />
      </div>
      <div className="anomaly-monitor-layout">
        <div className="anomaly-engine">
          <span className="monitor-label">WHEN IT RUNS</span>
          <strong>After every completed DQ run</strong>
          <p>It compares each approved rule’s failure rate without reading raw values in the browser.</p>
          <div className="anomaly-engine-state"><i /><span>{detectionMode}</span></div>
        </div>
        <div className="anomaly-evaluation">
          <div className="anomaly-spec-grid">
            <div><span>Minimum sample</span><strong>100 rows</strong><small>small checks are ignored</small></div>
            <div><span>Cold start</span><strong>≥ 5.0%</strong><small>until 5 prior runs exist</small></div>
            <div><span>Historical mode</span><strong>z ≥ 2.5</strong><small>also requires rate &gt; 1.0%</small></div>
          </div>
          {anomalies.length ? (
            <div className="anomaly-signal-list">
              {anomalies.map((anomaly) => (
                <article className="anomaly-monitor-signal" key={`${anomaly.rule_id}-${anomaly.anomaly_type}`}>
                  <div><strong>{anomaly.rule_title}</strong><span>{anomaly.anomaly_type === "Z_SCORE_SPIKE" ? "Historical spike" : "High violation rate"}</span></div>
                  <div className="anomaly-monitor-metrics"><span>Current <strong>{(anomaly.current_rate * 100).toFixed(2)}%</strong></span><span>{anomaly.historical_mean == null ? "Baseline unavailable" : <>Baseline <strong>{(anomaly.historical_mean * 100).toFixed(2)}%</strong></>}</span><span>{anomaly.z_score == null ? `${anomaly.history_size} prior runs` : <>z-score <strong>{anomaly.z_score.toFixed(2)}</strong></>}</span></div>
                  <p>{anomaly.reason}</p>
                </article>
              ))}
            </div>
          ) : (
            <div className="anomaly-clear-state"><span>Latest evaluation</span><strong>No unusual violation-rate movement detected.</strong><p>{historicalReady ? "Current rule rates remain within their stored historical baselines." : `Collect ${Math.max(0, 6 - trends.length)} more completed run${6 - trends.length === 1 ? "" : "s"} to enable historical z-score detection.`}</p></div>
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
  const latestScore = trends.at(-1)?.quality_score ?? profile?.validity_score ?? 0;
  const failedRules = results.filter((result) => result.status === "FAIL").length;
  const previousScore = trends.at(-2)?.quality_score;
  const scoreDelta = previousScore === undefined ? null : latestScore - previousScore;
  const sortedColumns = [...(profile?.columns ?? [])]
    .sort((left, right) => right.null_rate - left.null_rate)
    .slice(0, 8);
  const maximumViolation = results.reduce((maximum, result) => {
    const rate = result.checked_count ? result.failed_count / result.checked_count : 0;
    return Math.max(maximum, rate);
  }, 0);
  const circumference = 2 * Math.PI * 52;
  const scoreOffset = circumference * (1 - Math.min(100, Math.max(0, latestScore)) / 100);
  const latestRunAt = trends.at(-1)?.created_at;
  return (
    <>
      <div className="page-heading visualization-heading">
        <div>
          <span className="eyebrow">QUALITY CONTROL ROOM</span>
          <h1>Data quality observatory</h1>
          <p>Monitor run health, surface rule drift, and focus review on the signals that need attention.</p>
        </div>
        <div className="quality-dial" aria-label={`Latest quality score ${latestScore.toFixed(1)} percent`}>
          <svg viewBox="0 0 120 120" aria-hidden="true">
            <circle cx="60" cy="60" r="52" className="quality-dial-track" />
            <circle cx="60" cy="60" r="52" className="quality-dial-progress" strokeDasharray={circumference} strokeDashoffset={scoreOffset} />
          </svg>
          <div><strong>{latestScore.toFixed(1)}</strong><span>quality score</span></div>
        </div>
      </div>
      <section className="visual-kpi-rail" aria-label="Latest quality indicators">
        <div><span>Profiled records</span><strong>{(profile?.row_count ?? 0).toLocaleString()}</strong><small>current dataset</small></div>
        <div><span>Latest movement</span><strong className={scoreDelta !== null && scoreDelta < 0 ? "metric-warn" : ""}>{scoreDelta === null ? "Baseline" : `${scoreDelta >= 0 ? "+" : ""}${scoreDelta.toFixed(2)} pts`}</strong><small>{trends.length} completed {trends.length === 1 ? "run" : "runs"}</small></div>
        <div><span>Rules requiring review</span><strong className={failedRules ? "metric-warn" : ""}>{failedRules} / {results.length}</strong><small>{(maximumViolation * 100).toFixed(1)}% peak violation</small></div>
        <div><span>Signal status</span><strong className={anomalies.length ? "metric-warn" : ""}>{anomalies.length ? "Attention" : "Stable"}</strong><small>{anomalies.length} detected {anomalies.length === 1 ? "anomaly" : "anomalies"}</small></div>
      </section>
      <section className="visual-grid">
        <article className="panel trend-panel">
          <div className="panel-heading"><div><span className="eyebrow">RUN HISTORY</span><h3>Quality score trend</h3></div><span className="panel-caption">{latestRunAt ? `Updated ${formatTime(latestRunAt)}` : "No completed run"}</span></div>
          <TrendChart points={trends} />
          <div className="chart-legend"><span><i />Quality score</span><small>Calculated from bounded rule results</small></div>
        </article>
        <article className="panel signal-summary">
          <div className="signal-heading"><span className="eyebrow">LATEST SIGNALS</span><span className={`signal-state ${anomalies.length ? "attention" : "stable"}`}>{anomalies.length ? "Review" : "Stable"}</span></div>
          <div className="signal-number"><strong>{anomalies.length}</strong><span>anomalies detected</span></div>
          <div className="signal-row"><span>Failed rules</span><strong>{failedRules}</strong></div>
          <div className="signal-row"><span>Checks available</span><strong>{results.length}</strong></div>
          <div className="signal-row"><span>Detection mode</span><strong>{anomalies[0]?.detection_mode === "HISTORICAL" ? "Historical" : "Cold start"}</strong></div>
          <p className="signal-insight">{anomalies[0]?.reason ?? "No abnormal violation-rate movement detected in the latest completed run."}</p>
        </article>
        <article className="panel completeness-panel">
          <div className="panel-heading"><div><span className="eyebrow">PROFILE HEALTH</span><h3>Column completeness</h3></div><span className="panel-caption">lowest coverage first</span></div>
          <div className="viz-bars">
            {sortedColumns.map((column) => {
              const completeness = Math.max(0, 100 - column.null_rate * 100);
              return <div className="viz-bar-row" key={column.name}><span>{column.name}</span><div><i style={{ width: `${completeness}%` }} /></div><strong>{completeness.toFixed(1)}%</strong></div>;
            })}
            {!profile && <div className="chart-empty">Create a dataset profile to visualize completeness.</div>}
          </div>
        </article>
        <article className="panel failure-panel">
          <div className="panel-heading"><div><span className="eyebrow">RULE EXECUTION</span><h3>Violation rates</h3></div><span className="panel-caption">latest completed run</span></div>
          <div className="failure-list">
            {results.map((result) => {
              const rate = result.checked_count ? result.failed_count / result.checked_count : 0;
              return <div className="failure-item" key={result.rule_id}><div className="failure-copy"><strong title={result.rule_title}>{result.rule_title}</strong><span>{result.failed_count.toLocaleString()} of {result.checked_count.toLocaleString()} rows</span></div><strong className={rate ? "metric-warn" : ""}>{(rate * 100).toFixed(2)}%</strong><div className="failure-track"><i style={{ width: `${Math.min(100, rate * 100)}%` }} /></div></div>;
            })}
            {!results.length && <div className="chart-empty">No persisted rule results yet.</div>}
          </div>
        </article>
        <AnomalyMonitoringPanel anomalies={anomalies} trends={trends} />
      </section>
    </>
  );
}

function rowHasQualityIssue(row: DatasetRow) {
  return (row.trip_distance ?? 0) < 0
    || (row.fare_amount ?? 0) < 0
    || Boolean(row.payment_type?.startsWith("Invalid"))
    || Boolean(row.pickup_at && row.dropoff_at && row.pickup_at > row.dropoff_at)
    || !row.vendor_id;
}

function DataExplorerPage({ dataset }: { dataset?: Dataset }) {
  const [query, setQuery] = useState<DatasetRowQuery>({ quality_status: "ALL", sort_by: "pickup_at", sort_direction: "desc", limit: 25, offset: 0 });
  const [response, setResponse] = useState<DatasetRowsResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [queryError, setQueryError] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);

  const loadRows = useCallback(async (nextQuery: DatasetRowQuery) => {
    if (!dataset) return;
    setBusy(true);
    setQueryError("");
    try {
      setResponse(await api.queryDatasetRows(dataset.id, nextQuery));
      setQuery(nextQuery);
    } catch (requestError) {
      setQueryError(getErrorMessage(requestError, "Unable to query dataset rows."));
    } finally {
      setBusy(false);
    }
  }, [dataset]);

  useEffect(() => { if (dataset) void loadRows(query); }, [dataset, loadRows]);
  const page = response ? Math.floor(response.offset / response.limit) + 1 : 1;
  const pageCount = response ? Math.max(1, Math.ceil(response.total / response.limit)) : 1;
  const updateFilter = (key: keyof DatasetRowQuery, value: string | number | undefined) => setQuery((current) => ({ ...current, [key]: value, offset: 0 }));
  const activeFilterCount = [
    query.quality_status !== "ALL",
    Boolean(query.vendor_id),
    Boolean(query.payment_type),
    query.min_distance !== undefined,
    query.max_distance !== undefined,
    query.sort_by !== "pickup_at",
  ].filter(Boolean).length;
  const filterSummary = [
    query.quality_status === "ALL" ? "All rows" : query.quality_status === "ISSUE" ? "Issues only" : "Valid only",
    query.vendor_id ? `Vendor: ${query.vendor_id}` : "Any vendor",
    query.payment_type ? `Payment: ${query.payment_type}` : "Any payment",
  ].join(" · ");

  return (
    <>
      <div className="page-heading data-explorer-heading">
        <div><span className="eyebrow">BOUNDED READ ACCESS</span><h1>Data explorer</h1><p>Inspect a safe field projection with server-side filters and pagination.</p></div>
        <span className="data-count">{response?.total.toLocaleString() ?? "—"}<small>matching rows</small></span>
      </div>
      <section className={`panel filter-panel ${filtersOpen ? "is-open" : "is-collapsed"}`}>
        <div className="filter-toolbar">
          <div className="filter-toolbar-copy">
            <span className="eyebrow">QUERY CONTROLS</span>
            <button type="button" className="filter-toggle" aria-expanded={filtersOpen} aria-controls="data-explorer-filters" onClick={() => setFiltersOpen((open) => !open)}>
              <span className="filter-toggle-icon" aria-hidden="true">{filtersOpen ? "−" : "+"}</span>
              <span>{filtersOpen ? "Hide filters" : "Filter rows"}</span>
            </button>
            {!filtersOpen && <span className="filter-summary">{filterSummary}</span>}
          </div>
          <div className="filter-toolbar-state"><span className={activeFilterCount ? "filter-active-count" : "filter-default-state"}>{activeFilterCount ? `${activeFilterCount} active` : "Default view"}</span><span>Read-only</span></div>
        </div>
        {filtersOpen && <form id="data-explorer-filters" onSubmit={(event) => { event.preventDefault(); void loadRows({ ...query, offset: 0 }); }}>
          <label>Quality<select value={query.quality_status} onChange={(event) => updateFilter("quality_status", event.target.value)}><option value="ALL">All rows</option><option value="ISSUE">Issues only</option><option value="VALID">Valid only</option></select></label>
          <label>Vendor<select value={query.vendor_id ?? ""} onChange={(event) => updateFilter("vendor_id", event.target.value || undefined)}><option value="">Any vendor</option><option>Curb Mobility, LLC</option><option>Creative Mobile Technologies, LLC</option><option>Unknown Vendor</option></select></label>
          <label>Payment<select value={query.payment_type ?? ""} onChange={(event) => updateFilter("payment_type", event.target.value || undefined)}><option value="">Any payment</option><option>Flex Fare trip</option><option>Credit card</option><option>Cash</option><option>No charge</option><option>Dispute</option><option>Invalid Payment (Dispute/Test)</option></select></label>
          <label>Min distance<input type="number" step="0.1" value={query.min_distance ?? ""} onChange={(event) => updateFilter("min_distance", event.target.value === "" ? undefined : Number(event.target.value))} placeholder="No minimum" /></label>
          <label>Max distance<input type="number" step="0.1" value={query.max_distance ?? ""} onChange={(event) => updateFilter("max_distance", event.target.value === "" ? undefined : Number(event.target.value))} placeholder="No maximum" /></label>
          <label>Sort by<select value={query.sort_by} onChange={(event) => updateFilter("sort_by", event.target.value)}><option value="pickup_at">Pickup time</option><option value="trip_distance">Distance</option><option value="fare_amount">Fare</option><option value="total_amount">Total</option></select></label>
          <button className="button primary" disabled={busy}>{busy ? "Querying…" : "Apply filters"}</button>
        </form>}
        {filtersOpen && <div className="filter-note">Maximum 100 rows per request · allow-listed fields · read-only query</div>}
      </section>
      {queryError && <div className="alert error"><strong>Query failed</strong><span>{queryError}</span></div>}
      <section className="panel data-panel">
        <div className="panel-heading"><div><span className="eyebrow">QUERY RESULT</span><h3>Dataset rows</h3></div><span className="panel-caption">page {page} / {pageCount}</span></div>
        {busy && !response ? <div className="data-skeleton">Loading bounded dataset projection…</div> : response?.rows.length ? (
          <div className="data-table-wrap"><table className="data-table"><colgroup><col className="data-col-status" /><col className="data-col-row-id" /><col className="data-col-pickup" /><col className="data-col-vendor" /><col className="data-col-payment" /><col className="data-col-number" /><col className="data-col-number" /><col className="data-col-number" /></colgroup><thead><tr><th>Status</th><th>Row ID</th><th>Pickup</th><th>Vendor</th><th>Payment</th><th>Distance</th><th>Fare</th><th>Total</th></tr></thead><tbody>{response.rows.map((row) => { const issue = rowHasQualityIssue(row); return <tr key={row.source_row_id}><td><StatusPill label={issue ? "ISSUE" : "VALID"} tone={issue ? "warning" : "success"} /></td><td><code>{row.source_row_id}</code></td><td title={row.pickup_at ? new Date(row.pickup_at).toLocaleString() : undefined}>{row.pickup_at ? new Date(row.pickup_at).toLocaleString() : "—"}</td><td title={row.vendor_id}>{row.vendor_id ?? "—"}</td><td title={row.payment_type}>{row.payment_type ?? "—"}</td><td className={(row.trip_distance ?? 0) < 0 ? "metric-warn" : ""}>{row.trip_distance?.toFixed(2) ?? "—"}</td><td className={(row.fare_amount ?? 0) < 0 ? "metric-warn" : ""}>{row.fare_amount?.toFixed(2) ?? "—"}</td><td>{row.total_amount?.toFixed(2) ?? "—"}</td></tr>; })}</tbody></table></div>
        ) : <div className="table-empty">No rows match the current filters.</div>}
        <div className="pagination"><button className="button ghost" disabled={!response || response.offset === 0 || busy} onClick={() => void loadRows({ ...query, offset: Math.max(0, (response?.offset ?? 0) - (response?.limit ?? 25)) })}>← Previous</button><span>{response ? `${response.offset + 1}–${Math.min(response.offset + response.limit, response.total)} of ${response.total.toLocaleString()}` : "No result"}</span><button className="button ghost" disabled={!response || response.offset + response.limit >= response.total || busy} onClick={() => void loadRows({ ...query, offset: (response?.offset ?? 0) + (response?.limit ?? 25) })}>Next →</button></div>
      </section>
    </>
  );
}

function AdminPage({ users, access, loading, onCreate, onUpdate, onGrant, onRevoke }: { users: UserAccount[]; access: DatasetAccess[]; loading: boolean; onCreate: (input: UserCreateInput) => void; onUpdate: (username: string, input: UserUpdateInput) => void; onGrant: (username: string, level: DatasetAccessLevel) => void; onRevoke: (username: string) => void }) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<UserRole>("USER");
  const [grantUsername, setGrantUsername] = useState("");
  const [grantLevel, setGrantLevel] = useState<DatasetAccessLevel>("READ");
  const grantedNames = new Set(access.map((item) => item.username));
  return <><div className="page-heading"><div><span className="eyebrow">ADMINISTRATION</span><h1>Accounts and access</h1><p>Provision local demo users and grant read or manage access to the registered dataset.</p></div></div><div className="admin-grid"><section className="panel"><div className="panel-heading"><div><span className="eyebrow">ACCOUNT DIRECTORY</span><h3>Local users</h3></div><span className="panel-caption">{users.length} accounts</span></div><form className="admin-form" onSubmit={(event) => { event.preventDefault(); onCreate({ username, display_name: displayName, password, role }); setUsername(""); setDisplayName(""); setPassword(""); }}><input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="username" required /><input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="display name" required /><input value={password} onChange={(event) => setPassword(event.target.value)} placeholder="password (8+ chars)" type="password" minLength={8} required /><select value={role} onChange={(event) => setRole(event.target.value as UserRole)}><option>USER</option><option>STEWARD</option><option>ADMIN</option></select><button className="button primary">Create account</button></form><div className="admin-list">{loading ? <div className="table-empty">Loading accounts…</div> : users.map((user) => <AdminUserRow key={user.id} user={user} onUpdate={onUpdate} />)}</div></section><section className="panel"><div className="panel-heading"><div><span className="eyebrow">DATASET ACCESS</span><h3>Registered artifact</h3></div><span className="panel-caption">{access.length} grants</span></div><form className="admin-form grant" onSubmit={(event) => { event.preventDefault(); if (grantUsername) onGrant(grantUsername, grantLevel); }}><select value={grantUsername} onChange={(event) => setGrantUsername(event.target.value)} required><option value="">Select account</option>{users.filter((user) => !grantedNames.has(user.username)).map((user) => <option key={user.username} value={user.username}>{user.username} · {user.role}</option>)}</select><select value={grantLevel} onChange={(event) => setGrantLevel(event.target.value as DatasetAccessLevel)}><option value="READ">Read</option><option value="MANAGE">Manage</option></select><button className="button primary">Grant access</button></form><div className="admin-list">{access.map((grant) => <div className="admin-row" key={grant.id}><div><strong>{grant.display_name}</strong><small>{grant.username} · {grant.role}</small></div><span className="status-pill info"><span className="status-dot" />{grant.access_level}</span><button className="button ghost" onClick={() => onRevoke(grant.username)}>Revoke</button></div>)}</div></section></div></>;
}

function AdminUserRow({ user, onUpdate }: { user: UserAccount; onUpdate: (username: string, input: UserUpdateInput) => void }) {
  const [role, setRole] = useState<UserRole>(user.role);
  const [status, setStatus] = useState(user.status);
  return <div className="admin-row"><div><strong>{user.display_name}</strong><small>{user.username} · created {formatTime(user.created_at)}</small></div><select value={role} onChange={(event) => setRole(event.target.value as UserRole)}><option>USER</option><option>STEWARD</option><option>ADMIN</option></select><select value={status} onChange={(event) => setStatus(event.target.value as typeof status)}><option>ACTIVE</option><option>SUSPENDED</option><option>DISABLED</option></select><button className="button ghost" onClick={() => onUpdate(user.username, { role, status })}>Save</button></div>;
}

function AuditPage({ logs }: { logs: AuditLog[] }) {
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

