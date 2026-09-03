import type {
  ApiClient,
  AuditLog,
  CreateJobResponse,
  DataDictionary,
  SemanticContractConfirmInput,
  Dataset,
  DatasetProfile,
  DqResult,
  DqAnomaly,
  DatasetRow,
  DatasetRowQuery,
  DatasetRowsResponse,
  QualityTrendPoint,
  DqRun,
  DqRunCreateResponse,
  ActiveRule,
  Job,
  ManualRuleInput,
  ReviewInput,
  RuleProposal,
  RuleConfiguration,
  RuleConfigurationInput,
  SessionResponse,
  DatasetAccess,
  DatasetAccessLevel,
  UserAccount,
  UserCreateInput,
  UserUpdateInput,
  WorkflowRun,
  WorkflowStep,
  WorkflowStepKey,
  AgentArtifact,
  ArtifactReviewInput,
  LoopDecisionInput,
  GraphCatalog,
  GraphKey,
  GraphNodeSpec,
  NodeKind,
  NodeRun,
  NodeRunStatus,
} from "../types";

const datasetId = "dataset-nyc-yellow-taxi-50k";
const now = () => new Date().toISOString();
const wait = (duration = 250) => new Promise((resolve) => window.setTimeout(resolve, duration));

const dataset: Dataset = {
  id: datasetId,
  name: "NYC Yellow Taxi · Gate 2 artifact",
  description: "A deterministic 50k-row mobility dataset with a fixed manifest and synthetic quality mutations.",
  status: "REGISTERED",
  row_count: 50000,
  source_label: "NYC TLC Yellow Taxi · pinned source",
  manifest_version: "gate2-v1",
  checksum: "sha256:7a4d…c91e",
  updated_at: now(),
};

const profile: DatasetProfile = {
  dataset_id: datasetId,
  row_count: 50000,
  completeness_score: 96.8,
  validity_score: 91.4,
  duplicate_rate: 0.42,
  columns: [
    { name: "tpep_pickup_datetime", data_type: "timestamp", null_rate: 0, distinct_count: 48702, sample_value: "2024-01-03 08:14" },
    { name: "tpep_dropoff_datetime", data_type: "timestamp", null_rate: 0.18, distinct_count: 48620, sample_value: "2024-01-03 08:31" },
    { name: "trip_distance", data_type: "numeric", null_rate: 0.12, distinct_count: 12708, sample_value: "2.4" },
    { name: "fare_amount", data_type: "numeric", null_rate: 0, distinct_count: 9820, sample_value: "14.60" },
    { name: "payment_type", data_type: "category", null_rate: 0.28, distinct_count: 7, sample_value: "2" },
  ],
  evidence_keys: ["profile.row_count", "profile.trip_distance.negative_rate", "profile.payment_type.invalid_rate", "profile.duplicate_fingerprint_rate"],
  generated_at: now(),
};

const makeProposals = (): RuleProposal[] => [
  {
    id: "proposal-range",
    dataset_id: datasetId,
    title: "Trip distance must be non-negative",
    description: "Phát hiện các chuyến có quãng đường nhỏ hơn 0; hồ sơ tổng hợp đã ghi nhận một tỷ lệ nhỏ giá trị âm.",
    severity: "HIGH",
    status: "PROPOSED",
    rule: { type: "numeric_range", column: "trip_distance", min_value: 0 },
    evidence_refs: ["profile.trip_distance.negative_rate"],
    evidence_summary: "0,62% trên 50.000 dòng có quãng đường nhỏ hơn 0.",
    confidence: 0.97,
    model_name: "approved-model",
    rule_name: "Khoảng cách chuyến đi không được âm",
    business_rationale: "Giá trị âm làm sai lệch quãng đường, chi phí và các chỉ số vận hành của chuyến đi.",
    proposal_basis: "MIXED",
    parameter_provenance: [{ parameter_name: "min", source_type: "POLICY", source_ref: "policy.nonnegative_column.trip_distance", derivation_method: "Theo chính sách trường đo lường không âm" }],
    assumptions: ["Quãng đường chuyến đi được ghi nhận theo cùng một đơn vị đo trong toàn bộ tập dữ liệu."],
    confidence_breakdown: { overall: 0.97, evidence_strength: 0.98, business_support: 0.96, sample_representativeness: 0.97, explanation: "Bằng chứng hồ sơ và chính sách dữ liệu cùng hỗ trợ ngưỡng tối thiểu bằng 0." },
    source: "AGENT",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "proposal-null",
    dataset_id: datasetId,
    title: "Pickup timestamp is required",
    description: "Yêu cầu thời điểm đón khách để mỗi chuyến đi được xác định trong một khung thời gian.",
    severity: "MEDIUM",
    status: "PROPOSED",
    rule: { type: "not_null", column: "tpep_pickup_datetime" },
    evidence_refs: ["profile.row_count"],
    evidence_summary: "Độ đầy đủ của thời điểm đón khách đang thấp hơn ngưỡng trong hợp đồng dữ liệu.",
    confidence: 0.9,
    model_name: "approved-model",
    rule_name: "Thời điểm đón khách phải có đầy đủ",
    business_rationale: "Thiếu thời điểm đón khiến hệ thống không thể xác định khung thời gian và đối soát hành trình.",
    proposal_basis: "MIXED",
    parameter_provenance: [{ parameter_name: "column", source_type: "DATA_PROFILE", source_ref: "profile.column.tpep_pickup_datetime.null_rate", derivation_method: "Đối chiếu tỷ lệ khuyết thiếu trong hồ sơ" }],
    assumptions: ["Mỗi bản ghi hợp lệ cần có thời điểm đón khách."],
    confidence_breakdown: { overall: 0.9, evidence_strength: 0.88, business_support: 0.92, sample_representativeness: 0.9, explanation: "Hồ sơ dữ liệu cho thấy trường thời điểm là đầu vào cần thiết để định vị chuyến đi." },
    source: "AGENT",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "proposal-values",
    dataset_id: datasetId,
    title: "Payment type uses the approved vocabulary",
    description: "Từ chối các loại thanh toán không thuộc danh mục giá trị đã đăng ký của tập dữ liệu.",
    severity: "MEDIUM",
    status: "PROPOSED",
    rule: {
      type: "accepted_values",
      column: "payment_type",
      allowed_values: ["Flex Fare trip", "Credit card", "Cash", "No charge", "Dispute", "Unknown", "Voided trip"],
    },
    evidence_refs: ["profile.payment_type.invalid_rate"],
    evidence_summary: "0,28% số dòng chứa loại thanh toán không được nhận diện.",
    confidence: 0.94,
    model_name: "approved-model",
    rule_name: "Loại thanh toán phải thuộc danh mục cho phép",
    business_rationale: "Giá trị thanh toán ngoài danh mục làm sai lệch báo cáo doanh thu và gây khó khăn khi đối soát giao dịch.",
    proposal_basis: "POLICY",
    parameter_provenance: [{ parameter_name: "allowed_values", source_type: "POLICY", source_ref: "policy.governed_value_set.payment_type", derivation_method: "Sử dụng danh mục giá trị được quản trị" }],
    assumptions: ["Danh mục loại thanh toán đã đăng ký là nguồn chuẩn cho tập dữ liệu này."],
    confidence_breakdown: { overall: 0.94, evidence_strength: 0.95, business_support: 0.94, sample_representativeness: 0.93, explanation: "Danh mục được quản trị và tỷ lệ ngoài miền đã được lưu trong hồ sơ." },
    source: "AGENT",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "proposal-chronology",
    dataset_id: datasetId,
    title: "Dropoff occurs after pickup",
    description: "Yêu cầu thời điểm trả khách phải sau thời điểm đón khách.",
    severity: "HIGH",
    status: "PROPOSED",
    rule: { type: "cross_field_comparison", columns: ["tpep_pickup_datetime", "tpep_dropoff_datetime"], operator: "<" },
    evidence_refs: ["profile.dropoff_before_pickup_rate"],
    evidence_summary: "0,19% số dòng có thời điểm trả khách sớm hơn thời điểm đón.",
    confidence: 0.96,
    model_name: "approved-model",
    rule_name: "Thời điểm trả khách phải sau thời điểm đón",
    business_rationale: "Thứ tự thời gian sai làm hỏng thời lượng chuyến đi và các phân tích vận hành theo hành trình.",
    proposal_basis: "POLICY",
    parameter_provenance: [{ parameter_name: "operator", source_type: "POLICY", source_ref: "policy.cross_field.tpep_pickup_datetime.<.tpep_dropoff_datetime", derivation_method: "Áp dụng quan hệ thứ tự thời gian trong chính sách dữ liệu" }],
    assumptions: ["Hai trường thời gian dùng cùng múi giờ hoặc đã được chuẩn hóa trước khi kiểm tra."],
    confidence_breakdown: { overall: 0.96, evidence_strength: 0.95, business_support: 0.98, sample_representativeness: 0.95, explanation: "Quan hệ thứ tự được chính sách xác định và hồ sơ đã ghi nhận tỷ lệ vi phạm." },
    source: "AGENT",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "proposal-duplicate",
    dataset_id: datasetId,
    title: "Trip fingerprint should be unique",
    description: "Phát hiện các dấu vân tay nghiệp vụ lặp lại trên thời điểm đón, thời điểm trả và quãng đường.",
    severity: "LOW",
    status: "PROPOSED",
    rule: { type: "duplicate_fingerprint", fingerprint_columns: ["tpep_pickup_datetime", "tpep_dropoff_datetime", "trip_distance"] },
    evidence_refs: ["profile.duplicate_fingerprint_rate"],
    evidence_summary: "Tỷ lệ dấu vân tay trùng là 0,42% trên artifact đã đăng ký.",
    confidence: 0.88,
    model_name: "approved-model",
    rule_name: "Dấu vân tay chuyến đi phải duy nhất",
    business_rationale: "Bản ghi trùng có thể làm đếm lặp số chuyến, doanh thu và các chỉ số chất lượng dữ liệu.",
    proposal_basis: "MIXED",
    parameter_provenance: [{ parameter_name: "fingerprint_columns", source_type: "POLICY", source_ref: "policy.duplicate_fingerprint", derivation_method: "Sử dụng các trường định danh nghiệp vụ do chính sách chỉ định" }],
    assumptions: ["Ba trường dấu vân tay kết hợp đủ để nhận diện một chuyến đi trong tập dữ liệu."],
    confidence_breakdown: { overall: 0.88, evidence_strength: 0.86, business_support: 0.9, sample_representativeness: 0.88, explanation: "Hồ sơ đã đo tỷ lệ trùng trên cùng bộ trường định danh nghiệp vụ." },
    source: "AGENT",
    created_at: now(),
    updated_at: now(),
  },
];

let proposals = makeProposals();
let jobs: Job[] = [];
let runs: DqRun[] = [];
let results = new Map<string, DqResult[]>();
let anomalies = new Map<string, DqAnomaly[]>();
let activeRules: ActiveRule[] = [];
let auditLogs: AuditLog[] = [];
let configurations: RuleConfiguration[] = [];
let currentUsername = "";
let currentRole = "USER";
let workflowRuns: WorkflowRun[] = [];
let workflowArtifacts: AgentArtifact[] = [];
let accounts: Array<UserAccount & { password: string }> = [
  { id: "user-user", username: "user", display_name: "User", password: "user", role: "USER", status: "ACTIVE", created_by: "system-seed", created_at: now(), updated_at: now() },
  { id: "user-steward", username: "steward", display_name: "Steward", password: "steward", role: "STEWARD", status: "ACTIVE", created_by: "system-seed", created_at: now(), updated_at: now() },
  { id: "user-admin", username: "admin", display_name: "Admin", password: "admin", role: "ADMIN", status: "ACTIVE", created_by: "system-seed", created_at: now(), updated_at: now() },
];
let access: DatasetAccess[] = [
  { id: "access-user", dataset_id: datasetId, username: "user", display_name: "User", role: "USER", access_level: "READ", granted_by: "system-seed", granted_at: now() },
  { id: "access-steward", dataset_id: datasetId, username: "steward", display_name: "Steward", role: "STEWARD", access_level: "MANAGE", granted_by: "system-seed", granted_at: now() },
];

const paymentValues = ["Credit card", "Cash", "No charge", "Dispute"];
const mockRows: DatasetRow[] = Array.from({ length: 240 }, (_, index) => {
  const pickup = new Date(Date.UTC(2025, 0, 1, 6 + (index % 18), index % 60));
  const hasIssue = index % 37 === 0;
  const distance = hasIssue ? -1 * (1 + (index % 4) / 10) : Number((0.6 + (index % 48) * 0.31).toFixed(2));
  const fare = hasIssue && index % 2 === 0 ? -8.5 : Number((4.25 + Math.max(distance, 0) * 2.8).toFixed(2));
  return {
    source_row_id: `row-${String(index + 1).padStart(5, "0")}`,
    vendor_id: index % 2 ? "Creative Mobile Technologies, LLC" : "Curb Mobility, LLC",
    pickup_at: pickup.toISOString(),
    dropoff_at: new Date(pickup.getTime() + (8 + (index % 34)) * 60_000).toISOString(),
    passenger_count: 1 + (index % 4),
    trip_distance: distance,
    payment_type: hasIssue && index % 3 === 0 ? "Invalid Payment (Dispute/Test)" : paymentValues[index % paymentValues.length],
    fare_amount: fare,
    total_amount: Number((fare + 3.8).toFixed(2)),
  };
});

function rowHasIssue(row: DatasetRow) {
  return (row.trip_distance ?? 0) < 0 || (row.fare_amount ?? 0) < 0 || row.payment_type?.startsWith("Invalid");
}

function addAudit(action: string, entityType: string, entityId: string, summary: string) {
  auditLogs = [{ id: `audit-${Date.now()}`, action, entity_type: entityType, entity_id: entityId, actor: currentUsername || "local-system", summary, created_at: now() }, ...auditLogs];
}

function ensureAdmin() { if (currentRole !== "ADMIN") throw new Error("Only an administrator can manage accounts and access."); }

function makeJob(type: Job["type"]): Job {
  const timestamp = now();
  const job: Job = { id: `job-${Date.now()}-${Math.random().toString(16).slice(2)}`, type, status: "PENDING", progress: 0, message: "Queued for local worker…", created_at: timestamp, updated_at: timestamp };
  jobs = [...jobs, job];
  return job;
}

async function finishJob(jobId: string, type: Job["type"]) {
  const progressMessages = type === "INGEST_PROFILE"
    ? ["Validating manifest…", "Loading immutable raw rows…", "Running dbt build…", "Persisting aggregate profile…"]
    : type === "PROPOSE_RULES"
      ? ["Preparing allow-listed evidence…", "Calling local proposal adapter…", "Validating typed proposals…", "Persisting proposals…"]
      : type === "ANALYSIS_GRAPH2_GRAPH3"
        ? ["Loading Graph 2 evidence…", "Detecting anomalies…", "Investigating root causes…", "Writing steward report…"]
        : ["Claiming approved rule set…", "Compiling read-only checks…", "Executing bounded queries…", "Persisting results…"];

  for (let index = 0; index < progressMessages.length; index += 1) {
    await wait(420);
    jobs = jobs.map((item) => item.id === jobId ? { ...item, status: "RUNNING", progress: Math.round(((index + 1) / progressMessages.length) * 100), message: progressMessages[index], updated_at: now() } : item);
  }
  jobs = jobs.map((item) => item.id === jobId ? { ...item, status: "SUCCEEDED", progress: 100, message: "Completed", updated_at: now() } : item);
}

const workflowKeys: WorkflowStepKey[] = [
  "UPLOAD_PROFILE",
  "UNDERSTAND_DATA",
  "PROPOSE_RULES",
  "REVIEW_RULES",
  "PUBLISH_RULESET",
  "RUN_CHECKS",
  "ANALYZE_REPORT",
];

function makeWorkflow(id: string, workflowDatasetId = datasetId): WorkflowRun {
  const steps: WorkflowStep[] = workflowKeys.map((key, index) => ({
    key,
    status: index === 0 ? "READY" : "LOCKED",
    artifact_ids: [],
  }));
  return { id, dataset_id: workflowDatasetId, current_step: "UPLOAD_PROFILE", iteration: 0, max_iterations: 3, steps };
}

function advanceWorkflow(workflow: WorkflowRun, completed: WorkflowStepKey, moveCurrent = true) {
  const index = workflow.steps.findIndex((step) => step.key === completed);
  workflow.steps = workflow.steps.map((step, stepIndex) => {
    if (step.key === completed) return { ...step, status: "COMPLETED", completed_at: now() };
    if (stepIndex === index + 1) return { ...step, status: "READY" };
    return step;
  });
  const next = workflow.steps[index + 1];
  if (next && moveCurrent) workflow.current_step = next.key;
}

function addWorkflowArtifact(workflow: WorkflowRun, artifact: AgentArtifact) {
  workflowArtifacts = [...workflowArtifacts, artifact];
  workflow.steps = workflow.steps.map((step) => step.key === workflow.current_step
    ? { ...step, artifact_ids: [...step.artifact_ids, artifact.id] }
    : step);
}

function clearTemporaryDownstreamSessions(workflow: WorkflowRun, targetStep: WorkflowStepKey) {
  const targetIndex = workflow.steps.findIndex((step) => step.key === targetStep);
  if (targetIndex < 0 || !workflow.steps.some((step, index) => index > targetIndex && step.temporary)) return;
  const downstreamArtifactIds = new Set(workflow.steps.slice(targetIndex + 1).flatMap((step) => step.artifact_ids));
  workflowArtifacts = workflowArtifacts.filter((artifact) => !downstreamArtifactIds.has(artifact.id));
  workflow.steps = workflow.steps.map((step, index) => index > targetIndex
    ? { ...step, status: "LOCKED", artifact_ids: [], temporary: false, blocker: undefined, started_at: undefined, completed_at: undefined }
    : { ...step, temporary: false });
  addAudit("WORKFLOW_TEMP_SESSIONS_CLEARED", "workflow", workflow.id, `Cleared temporary sessions after ${targetStep}.`);
}

function clearRuleChangeDownstreamSessions() {
  workflowRuns.forEach((workflow) => {
    if (workflow.current_step === "PROPOSE_RULES" || workflow.current_step === "REVIEW_RULES") {
      clearTemporaryDownstreamSessions(workflow, workflow.current_step);
    }
  });
}

function createArtifact(workflow: WorkflowRun, type: AgentArtifact["type"], agentRole: AgentArtifact["agent_role"], payload: unknown, status: AgentArtifact["status"] = "VALIDATED") {
  const artifact: AgentArtifact = {
    id: `artifact-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    workflow_run_id: workflow.id,
    agent_role: agentRole,
    type,
    version: 1,
    status,
    payload,
    created_at: now(),
  };
  addWorkflowArtifact(workflow, artifact);
  return artifact;
}

function completeWorkflowStep(workflowId: string, step: WorkflowStepKey) {
  const workflow = workflowRuns.find((item) => item.id === workflowId);
  if (!workflow || workflow.current_step !== step) return;
  let generatedArtifact: AgentArtifact | undefined;
  if (step === "UPLOAD_PROFILE") {
    dataset.status = "PROFILE_READY";
  } else if (step === "UNDERSTAND_DATA") {
    generatedArtifact = createArtifact(workflow, "SEMANTIC_CONTRACT", "DATA_RULE_AGENT", {
      summary: "Mobility trip dataset with timestamps, numeric measures and a controlled payment vocabulary.",
      columns: profile.columns.map((column) => ({ name: column.name, semantic_type: column.data_type === "numeric" ? "measure" : column.data_type === "timestamp" ? "event_time" : "category", confidence: 0.9 })),
      evidence: profile.evidence_keys,
    });
  } else if (step === "PROPOSE_RULES") {
    generatedArtifact = createArtifact(workflow, "RULE_SET", "DATA_RULE_AGENT", {
      proposal_count: proposals.length,
      rules: proposals.map((proposal) => ({ id: proposal.id, title: proposal.title, evidence: proposal.evidence_refs, confidence: proposal.confidence })),
    }, "DRAFT");
  } else if (step === "PUBLISH_RULESET") {
    activeRules = proposals
      .filter((proposal) => proposal.workflow_run_id === workflowId && proposal.status === "APPROVED")
      .map((proposal) => ({
        rule_id: proposal.id,
        dataset_id: proposal.dataset_id,
        table_name: proposal.dataset_id,
        column: "column" in proposal.rule ? proposal.rule.column : null,
        rule_type: proposal.rule.type,
        parameters: { ...proposal.rule },
        severity: proposal.severity,
        dimension: "VALIDITY",
        rule_description: proposal.description,
        status: "ACTIVE",
      }));
    generatedArtifact = createArtifact(workflow, "PUBLISHED_RULESET", "DATA_RULE_AGENT", {
      ruleset_id: `ruleset-${Date.now()}`,
      ruleset_hash: "mock-published-ruleset",
      rule_count: proposals.filter((proposal) => proposal.status === "APPROVED").length,
    }, "APPROVED");
  } else if (step === "RUN_CHECKS") {
    const approved = proposals.filter((proposal) => proposal.status === "APPROVED");
    const runId = `run-${Date.now()}`;
    const totalChecked = approved.length * mockRows.length;
    const totalFailed = approved.reduce((count, proposal) => count + (proposal.rule.type === "numeric_range" ? 7 : 0), 0);
    runs = [
      ...runs,
      {
        id: runId,
        job_id: `job-${runId}`,
        dataset_id: datasetId,
        rule_ids: approved.map((proposal) => proposal.id),
        status: "SUCCEEDED",
        total_checked: totalChecked,
        total_failed: totalFailed,
        created_at: now(),
        completed_at: now(),
      },
    ];
    generatedArtifact = createArtifact(workflow, "DQ_RUN", "DATA_RULE_AGENT", {
      run_id: runId,
      total_checked: totalChecked,
      total_failed: totalFailed,
      results: approved.map((proposal) => ({ rule_id: proposal.id, title: proposal.title, status: "PASS", failed_count: 0 })),
    });
    advanceWorkflow(workflow, step);
    workflowRuns = workflowRuns.map((item) => item.id === workflowId ? workflow : item);
    return;
  } else if (step === "ANALYZE_REPORT") {
    generatedArtifact = createArtifact(workflow, "ANOMALY_REPORT", "DATA_RULE_AGENT", {
      decision: "WATCH", confidence: 0.74,
      report_source: "LLM",
      report_markdown: "# Báo Cáo Data Steward\n\n## 1. Tóm Tắt Điều Hành\n\nĐây là báo cáo Markdown được sinh bởi Graph 3.\n",
      hypotheses: [{ summary: "The mock run found a bounded set of profile-consistent violations." }],
    }, "APPROVED");
    advanceWorkflow(workflow, step);
    workflowRuns = workflowRuns.map((item) => item.id === workflowId ? workflow : item);
    return;
  } else if (step === "PROPOSE_CODE") {
    generatedArtifact = createArtifact(workflow, "CODE_PROPOSAL", "STANDARDIZATION_AGENT", {
      language: "SQL",
      target: "staging.normalized_dataset",
      changes: ["trim categorical values", "normalize timestamps to UTC", "preserve raw columns"],
      validation: { deterministic: true, destructive: false },
    }, "DRAFT");
  } else if (step === "ANALYZE_IMPROVE") {
    generatedArtifact = createArtifact(workflow, "LOOP_RECOMMENDATION", "LOOP_AGENT", {
      hypothesis: "Negative distance violations are concentrated in a small cohort and should be reviewed before relaxing the rule.",
      supporting_signals: ["HIGH_VIOLATION_RATE", "profile.trip_distance.negative_rate"],
      next_action: "Review the range rule and rerun the bounded check.",
    }, "DRAFT");
  }
  const waitingApproval = step === "PROPOSE_RULES" || step === "PROPOSE_CODE" || step === "ANALYZE_IMPROVE";
  if (waitingApproval && step !== "ANALYZE_IMPROVE") {
    advanceWorkflow(workflow, step);
    const reviewStep = step === "PROPOSE_RULES" ? "REVIEW_RULES" : "REVIEW_EXECUTE";
    workflow.steps = workflow.steps.map((item) => item.key === reviewStep ? { ...item, status: "WAITING_APPROVAL", artifact_ids: generatedArtifact ? [...item.artifact_ids, generatedArtifact.id] : item.artifact_ids } : item);
  } else if (waitingApproval) {
    workflow.steps = workflow.steps.map((item) => item.key === step ? { ...item, status: "WAITING_APPROVAL" } : item);
  } else {
    advanceWorkflow(workflow, step, step !== "UNDERSTAND_DATA");
  }
  workflowRuns = workflowRuns.map((item) => item.id === workflowId ? workflow : item);
}

const mockDataDictionaries = new Map<string, DataDictionary>();

export const mockApi: ApiClient = {
  async createSession(username, password): Promise<SessionResponse> {
    await wait(300);
    const account = accounts.find((item) => item.username === username.trim().toLowerCase());
    if (!account || account.status !== "ACTIVE" || account.password !== password) throw new Error("Invalid username or password.");
    currentUsername = account.username;
    currentRole = account.role;
    addAudit("SESSION_STARTED", "session", "local-session", "Started a local Data Steward session.");
    return { username: account.username, role: account.role, csrf_token: "local-csrf-token", expires_at: new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString() };
  },
  async deleteSession() {
    await wait(150);
    addAudit("SESSION_ENDED", "session", "local-session", "Ended the local Data Steward session.");
    currentUsername = "";
    currentRole = "USER";
  },
  async listDatasets() {
    await wait(250);
    return [dataset];
  },
  async startIngestion(id, _idempotencyKey) {
    if (id !== datasetId) throw new Error("Dataset not found.");
    if (jobs.some((job) => job.type === "INGEST_PROFILE" && ["PENDING", "RUNNING"].includes(job.status))) throw new Error("An ingestion job is already active.");
    const job = makeJob("INGEST_PROFILE");
    void finishJob(job.id, job.type).then(() => { dataset.status = "PROFILE_READY"; addAudit("PROFILE_CREATED", "dataset", datasetId, "Created aggregate profile from the registered artifact."); });
    return { job_id: job.id, status: "PENDING" } satisfies CreateJobResponse;
  },
  async getJob(id) {
    await wait(100);
    const job = jobs.find((item) => item.id === id);
    if (!job) throw new Error("Job not found.");
    return job;
  },
  async getProfile(id) {
    await wait(200);
    if (id === datasetId && profile && dataset.status !== "PROFILE_READY") {
      dataset.status = "PROFILE_READY";
    }
    return id === datasetId && dataset.status === "PROFILE_READY" ? profile : null;
  },
  async startRuleProposals(id, _idempotencyKey) {
    if (id === datasetId && profile && dataset.status !== "PROFILE_READY") {
      dataset.status = "PROFILE_READY";
    }
    if (id !== datasetId || dataset.status !== "PROFILE_READY") throw new Error("A completed profile is required before requesting proposals.");
    const job = makeJob("PROPOSE_RULES");
    void finishJob(job.id, job.type).then(() => addAudit("PROPOSALS_CREATED", "dataset", datasetId, "Generated typed proposals from aggregate evidence."));
    return { job_id: job.id, status: "PENDING" } satisfies CreateJobResponse;
  },
  async listProposals(id, workflowRunId?) {
    await wait(200);
    const available = id === datasetId ? proposals : [];
    return workflowRunId
      ? available.filter((proposal) => proposal.workflow_run_id === workflowRunId)
      : available;
  },
  async createManualRule(id, input: ManualRuleInput) {
    if (id !== datasetId) throw new Error("Dataset not found.");
    const activeWorkflow = [...workflowRuns].reverse().find((item) => item.dataset_id === id && item.current_step === "REVIEW_RULES");
    const proposal: RuleProposal = { id: `manual-${Date.now()}`, dataset_id: datasetId, ...input, workflow_run_id: input.workflow_run_id ?? activeWorkflow?.id, status: "PROPOSED", source: "MANUAL", evidence_refs: [], evidence_summary: "Manually authored by the Data Steward; no agent evidence attached.", confidence: 1, model_name: "manual", created_at: now(), updated_at: now() };
    proposals = [...proposals, proposal];
    addAudit("MANUAL_RULE_CREATED", "rule_proposal", proposal.id, `Created manual rule “${proposal.title}”.`);
    return proposal;
  },
  async reviewProposal(id, input: ReviewInput) {
    await wait(220);
    const existing = proposals.find((proposal) => proposal.id === id);
    if (!existing) throw new Error("Proposal not found.");
    if (input.workflow_run_id && input.workflow_run_id !== existing.workflow_run_id) throw new Error("The proposal does not belong to this workflow.");
    clearRuleChangeDownstreamSessions();
    const status: RuleProposal["status"] = input.action === "approve" ? "APPROVED" : input.action === "reject" ? "REJECTED" : "EDITED";
    const updated = { ...existing, ...input, workflow_run_id: existing.workflow_run_id, status, rule: input.rule ?? existing.rule, updated_at: now() };
    delete (updated as Partial<ReviewInput>).action;
    proposals = proposals.map((proposal) => proposal.id === id ? updated : proposal);
    if (status === "APPROVED" && !configurations.some((item) => item.rule_id === id)) configurations = [...configurations, { rule_id: id, execution_status: "ACTIVE", schedule_frequency: "MANUAL", timezone: "UTC", updated_at: now() }];
    addAudit(`PROPOSAL_${status}`, "rule_proposal", id, `${status === "APPROVED" ? "Approved" : status === "REJECTED" ? "Rejected" : "Edited"} rule proposal “${updated.title}”.`);
    return updated;
  },
  async deleteProposal(id) {
    const proposal = proposals.find((item) => item.id === id);
    if (!proposal) throw new Error("Proposal not found.");
    if (proposal.status === "APPROVED") throw new Error("Reject an approved proposal before deleting it.");
    clearRuleChangeDownstreamSessions();
    proposals = proposals.filter((item) => item.id !== id);
    configurations = configurations.filter((item) => item.rule_id !== id);
    addAudit("PROPOSAL_DELETED", "rule_proposal", id, `Deleted rule proposal “${proposal.title}”.`);
  },
  async listRuleConfigurations(id) {
    return id === datasetId ? configurations : [];
  },
  async updateRuleConfiguration(id, input: RuleConfigurationInput) {
    const proposal = proposals.find((item) => item.id === id);
    if (!proposal || proposal.status !== "APPROVED") throw new Error("Only approved rules can be configured.");
    const updated: RuleConfiguration = { rule_id: id, ...input, updated_at: now() };
    configurations = configurations.some((item) => item.rule_id === id) ? configurations.map((item) => item.rule_id === id ? updated : item) : [...configurations, updated];
    addAudit("RULE_CONFIGURATION_UPDATED", "rule_configuration", id, `Updated execution settings for “${proposal.title}”.`);
    return updated;
  },
  async startDqRun(ruleIds, _idempotencyKey) {
    const approved = proposals.filter((proposal) => ruleIds.includes(proposal.id) && proposal.status === "APPROVED" && configurations.find((item) => item.rule_id === proposal.id)?.execution_status !== "PAUSED");
    if (!approved.length) throw new Error("At least one approved rule is required.");
    const job = makeJob("RUN_DQ");
    const run: DqRun = { id: `run-${Date.now()}`, job_id: job.id, dataset_id: datasetId, rule_ids: approved.map((proposal) => proposal.id), status: "PENDING", total_failed: 0, total_checked: 0, created_at: now() };
    runs = [...runs, run];
    void finishJob(job.id, job.type).then(() => {
      const runResults = approved.map((proposal, index): DqResult => ({ rule_id: proposal.id, rule_title: proposal.title, status: index === 1 ? "PASS" : "FAIL", checked_count: 50000, failed_count: index === 1 ? 0 : index === 0 ? 3100 : 140, failed_row_ids: index === 1 ? [] : [`row-${1000 + index}`, `row-${2000 + index}`, `row-${3000 + index}`] }));
      results.set(run.id, runResults);
      anomalies.set(
        run.id,
        runResults
          .filter((result) => result.checked_count > 0 && result.failed_count / result.checked_count >= 0.05)
          .map((result) => ({
            rule_id: result.rule_id,
            rule_title: result.rule_title,
            anomaly_type: "HIGH_VIOLATION_RATE" as const,
            current_rate: result.failed_count / result.checked_count,
            history_size: 0,
            detection_mode: "COLD_START" as const,
            checked_count: result.checked_count,
            failed_count: result.failed_count,
            reason: `Violation rate ${((result.failed_count / result.checked_count) * 100).toFixed(2)}% is elevated for this cold-start run.`,
          })),
      );
      const failed = runResults.reduce((sum, result) => sum + result.failed_count, 0);
      runs = runs.map((item) => item.id === run.id ? { ...item, status: "SUCCEEDED", total_checked: 50000 * approved.length, total_failed: failed, completed_at: now() } : item);
      addAudit("DQ_RUN_COMPLETED", "dq_run", run.id, `Completed a read-only run across ${approved.length} approved rules.`);
    });
    return { job_id: job.id, run_id: run.id, status: "PENDING" } satisfies DqRunCreateResponse;
  },
  async getDqRun(id) {
    await wait(120);
    const run = runs.find((item) => item.id === id);
    if (!run) throw new Error("DQ run not found.");
    return run;
  },
  async getDqResults(id) {
    await wait(180);
    return results.get(id) ?? [];
  },
  async getDqAnomalies(id) {
    await wait(120);
    return anomalies.get(id) ?? [];
  },
  // Điều tra nguyên nhân gốc do DeepAgent thực hiện trên backend thật. Chế độ giả
  // lập không dựng lại chuỗi suy luận đó — trả mảng rỗng để giao diện hiện đúng
  // trạng thái "chưa có", thay vì bịa ra giả thuyết trông như thật.
  async getActiveRules() {
    await wait(80);
    return structuredClone(activeRules.filter((rule) => rule.dataset_id === datasetId));
  },
  async getAnomalySignals() {
    await wait(80);
    return [];
  },
  async getAnomalyHypotheses() {
    await wait(80);
    return [];
  },
  async submitAnomalyFeedback() {
    await wait(80);
  },
  async getLatestDqRun(id) {
    await wait(100);
    return [...runs].reverse().find((run) => run.dataset_id === id) ?? null;
  },
  async getQualityTrends(id): Promise<QualityTrendPoint[]> {
    await wait(120);
    if (id !== datasetId) return [];
    const completed = runs.filter((run) => run.status === "SUCCEEDED");
    if (completed.length) {
      return completed.slice(-12).map((run) => {
        const failureRate = run.total_checked ? run.total_failed / run.total_checked : 0;
        return { run_id: run.id, created_at: run.created_at, quality_score: Number((100 * (1 - failureRate)).toFixed(2)), failure_rate: failureRate, total_checked: run.total_checked, total_failed: run.total_failed, rule_count: run.rule_ids.length };
      });
    }
    return Array.from({ length: 8 }, (_, index) => ({ run_id: `historical-${index}`, created_at: new Date(Date.now() - (7 - index) * 86_400_000).toISOString(), quality_score: 92.4 + index * 0.54 - (index % 3) * 0.37, failure_rate: 0.076 - index * 0.004, total_checked: 200000, total_failed: 15200 - index * 800, rule_count: 4 }));
  },
  async getDataDictionary(id): Promise<DataDictionary | null> {
    await wait(120);
    return mockDataDictionaries.get(id) ?? null;
  },
  async uploadDataDictionary(id, file): Promise<DataDictionary> {
    await wait(200);
    const record: DataDictionary = {
      id: `ddict-${id}`,
      dataset_id: id,
      source: "UPLOADED",
      source_filename: file.name,
      column_count: 0,
      tables: [{ table_name: id, description: "", columns: [], business_rules: [] }],
      updated_at: new Date().toISOString(),
    };
    mockDataDictionaries.set(id, record);
    return record;
  },
  async deleteDataDictionary(id): Promise<void> {
    await wait(120);
    mockDataDictionaries.delete(id);
  },
  async queryDatasetRows(id, query: DatasetRowQuery): Promise<DatasetRowsResponse> {
    await wait(160);
    if (id !== datasetId) throw new Error("Dataset not found.");
    let filtered = mockRows.filter((row) => {
      if (query.vendor_id && row.vendor_id !== query.vendor_id) return false;
      if (query.payment_type && row.payment_type !== query.payment_type) return false;
      if (query.min_distance !== undefined && (row.trip_distance ?? 0) < query.min_distance) return false;
      if (query.max_distance !== undefined && (row.trip_distance ?? 0) > query.max_distance) return false;
      if (query.filter_column && String(row[query.filter_column] ?? "") !== String(query.filter_value ?? "")) return false;
      if (query.quality_status === "ISSUE" && !rowHasIssue(row)) return false;
      if (query.quality_status === "VALID" && rowHasIssue(row)) return false;
      return true;
    });
    if (query.sort_by) {
      const direction = query.sort_direction === "asc" ? 1 : -1;
      const sortBy = query.sort_by;
      filtered = filtered.sort((left, right) => String(left[sortBy] ?? "").localeCompare(String(right[sortBy] ?? "")) * direction);
    }
    const offset = query.offset ?? 0;
    const limit = query.limit ?? 25;
    return { dataset_id: id, total: filtered.length, offset, limit, rows: filtered.slice(offset, offset + limit) };
  },
  async listAuditLogs() {
    await wait(180);
    return auditLogs;
  },
  async listUsers() { ensureAdmin(); return accounts.map(({ password: _password, ...account }) => account); },
  async createUser(input: UserCreateInput) {
    ensureAdmin(); const username = input.username.trim().toLowerCase();
    if (accounts.some((item) => item.username === username)) throw new Error("An account with this username already exists.");
    const account = { id: `user-${Date.now()}`, username, display_name: input.display_name.trim(), password: input.password, role: input.role, status: "ACTIVE" as const, created_by: currentUsername, created_at: now(), updated_at: now() };
    accounts = [...accounts, account]; addAudit("USER_CREATED", "user", account.id, `Created account '${username}'.`); const { password: _password, ...publicAccount } = account; return publicAccount;
  },
  async updateUser(username: string, input: UserUpdateInput) {
    ensureAdmin(); const existing = accounts.find((item) => item.username === username.toLowerCase());
    if (!existing) throw new Error("User not found.");
    if (existing.username === currentUsername && (input.status === "SUSPENDED" || input.status === "DISABLED" || (input.role && input.role !== "ADMIN"))) throw new Error("An admin cannot remove their own active admin access.");
    const updated = { ...existing, ...input, password: input.password ?? existing.password, updated_at: now() }; accounts = accounts.map((item) => item.username === existing.username ? updated : item);
    access = access.map((item) => item.username === updated.username ? { ...item, display_name: updated.display_name, role: updated.role } : item); addAudit("USER_UPDATED", "user", updated.id, `Updated account '${updated.username}'.`); const { password: _password, ...publicAccount } = updated; return publicAccount;
  },
  async listDatasetAccess(id: string) { ensureAdmin(); return id === datasetId ? access : []; },
  async grantDatasetAccess(id: string, username: string, accessLevel: DatasetAccessLevel) {
    ensureAdmin(); const account = accounts.find((item) => item.username === username.toLowerCase()); if (!account) throw new Error("User not found.");
    const existing = access.find((item) => item.dataset_id === id && item.username === account.username);
    const grant: DatasetAccess = existing ? { ...existing, access_level: accessLevel, granted_by: currentUsername, granted_at: now() } : { id: `access-${Date.now()}`, dataset_id: id, username: account.username, display_name: account.display_name, role: account.role, access_level: accessLevel, granted_by: currentUsername, granted_at: now() };
    access = existing ? access.map((item) => item.id === existing.id ? grant : item) : [...access, grant]; addAudit("DATASET_ACCESS_GRANTED", "dataset_access", grant.id, `Updated access for '${account.username}'.`); return grant;
  },
  async revokeDatasetAccess(id: string, username: string) { ensureAdmin(); const grant = access.find((item) => item.dataset_id === id && item.username === username.toLowerCase()); if (!grant) throw new Error("Dataset access grant not found."); access = access.filter((item) => item.id !== grant.id); addAudit("DATASET_ACCESS_REVOKED", "dataset_access", grant.id, `Revoked access for '${username}'.`); },
  async createWorkflow(id: string, fresh = false, _datasetVersionId?: string, freshProfile = false, _requestKey?: string) {
    if (freshProfile) throw new Error("Fresh source profiling requires the real local API, not the mock adapter.");
    await wait(180);
    const existing = fresh
      ? undefined
      : workflowRuns.find((item) => item.dataset_id === id && item.steps.some((step) => !["COMPLETED", "STALE"].includes(step.status)));
    if (existing) return structuredClone(existing);
    const workflow = makeWorkflow(`workflow-${Date.now()}`, id);
    // Dataset preparation is a deterministic system prerequisite. Complete it
    // automatically when the user starts the agent workflow so it does not
    // become a manual stage that must be rerun during navigation.
    workflow.steps = workflow.steps.map((step, index) => index === 0
      ? { ...step, status: "COMPLETED", completed_at: now() }
      : index === 1
        ? { ...step, status: "READY" }
        : step);
    workflow.current_step = "UNDERSTAND_DATA";
    dataset.status = "PROFILE_READY";
    workflowRuns = [...workflowRuns, workflow];
    addAudit("WORKFLOW_CREATED", "workflow", workflow.id, "Created a step-by-step agent workflow in the local adapter.");
    return structuredClone(workflow);
  },
  async getLatestWorkflow(id: string) {
    await wait(80);
    const workflow = [...workflowRuns].reverse().find((item) => item.dataset_id === id);
    return workflow ? structuredClone(workflow) : null;
  },
  async importDataset(file) {
    const imported = { ...dataset, id: `dataset-import-${Date.now()}`, name: file.name.replace(/\.[^.]+$/, ""), source_label: file.name, status: "PROFILE_READY" as const, updated_at: new Date().toISOString() };
    const job = makeJob("INGEST_PROFILE");
    return { dataset: imported, job: { job_id: job.id, status: "PENDING" as const } };
  },
  async deleteDataset(id: string) {
    await wait(100);
  },
  async getWorkflow(id: string) {
    await wait(120);
    const workflow = workflowRuns.find((item) => item.id === id);
    if (!workflow) throw new Error("Workflow run not found.");
    return structuredClone(workflow);
  },
  async runWorkflowStep(id: string, step: WorkflowStepKey, expectedDatasetId?: string, _expectedVersionId?: string) {
    const workflow = workflowRuns.find((item) => item.id === id);
    if (!workflow) throw new Error("Workflow run not found.");
    if (expectedDatasetId && workflow.dataset_id !== expectedDatasetId) throw new Error("Workflow dataset mismatch");
    if (workflow.current_step !== step) throw new Error(`Step ${step} is not ready. Complete ${workflow.current_step} first.`);
    const current = workflow.steps.find((item) => item.key === step);
    if (!current || !["READY", "FAILED", ...(step === "ANALYZE_REPORT" ? ["COMPLETED"] : [])].includes(current.status)) throw new Error("This workflow step is waiting for review.");
    clearTemporaryDownstreamSessions(workflow, step);
    if (step === "PROPOSE_RULES") {
      // The fixture starts with a deterministic proposal set. Attach that set
      // to the durable mock workflow so the real-client scoping rule is also
      // exercised when the local adapter is enabled.
      proposals = proposals.map((proposal) => ({ ...proposal, workflow_run_id: id }));
    }
    workflow.steps = workflow.steps.map((item) => item.key === step ? { ...item, status: "RUNNING", started_at: now() } : item);
    const job = makeJob(
      step === "UPLOAD_PROFILE"
        ? "INGEST_PROFILE"
        : step === "UNDERSTAND_DATA"
          ? "UNDERSTAND_DATA"
          : step === "PROPOSE_RULES"
            ? "PROPOSE_RULES"
            : step === "ANALYZE_REPORT"
              ? "ANALYSIS_GRAPH2_GRAPH3"
              : "RUN_DQ",
    );
    void finishJob(job.id, job.type).then(() => completeWorkflowStep(id, step));
    return { job_id: job.id, status: "PENDING" } satisfies CreateJobResponse;
  },
  async advanceWorkflowStep(id: string) {
    await wait(120);
    const workflow = workflowRuns.find((item) => item.id === id);
    if (!workflow) throw new Error("Workflow run not found.");
    const currentIndex = workflow.steps.findIndex((item) => item.key === workflow.current_step);
    const current = workflow.steps[currentIndex];
    const next = workflow.steps[currentIndex + 1];
    if (!current || current.status !== "COMPLETED" || !next || next.status !== "READY") throw new Error("The next workflow step is not ready.");
    workflow.current_step = next.key;
    workflowRuns = workflowRuns.map((item) => item.id === id ? workflow : item);
    addAudit("WORKFLOW_STEP_ADVANCED", "workflow", id, `Advanced from ${current.key} to ${next.key}.`);
    return structuredClone(workflow);
  },
  async listWorkflowArtifacts(id: string) {
    await wait(120);
    return workflowArtifacts.filter((item) => item.workflow_run_id === id).map((item) => structuredClone(item));
  },
  async reviewArtifact(id: string, input: ArtifactReviewInput) {
    await wait(180);
    const existing = workflowArtifacts.find((item) => item.id === id);
    if (!existing) throw new Error("Workflow artifact not found.");
    const workflow = workflowRuns.find((item) => item.id === existing.workflow_run_id);
    if (!workflow) throw new Error("Workflow run not found.");
    if (existing.type === "RULE_SET" && input.action === "approve") {
      const unresolved = proposals.some((proposal) => ["PROPOSED", "EDITED"].includes(proposal.status));
      if (unresolved) throw new Error("Decide every proposed rule before confirming the rule set.");
      if (!proposals.some((proposal) => proposal.status === "APPROVED")) throw new Error("At least one rule must be approved before continuing.");
    }
    const approvalStep = existing.type === "RULE_SET" ? "REVIEW_RULES" : existing.type === "CODE_PROPOSAL" ? "REVIEW_EXECUTE" : existing.type === "LOOP_RECOMMENDATION" ? "ANALYZE_IMPROVE" : null;
    if (input.action === "approve" && approvalStep && workflow.current_step === approvalStep) clearTemporaryDownstreamSessions(workflow, approvalStep);
    const status: AgentArtifact["status"] = input.action === "approve" ? "APPROVED" : input.action === "reject" ? "REJECTED" : "DRAFT";
    const updated = { ...existing, status };
    workflowArtifacts = workflowArtifacts.map((item) => item.id === id ? updated : item);
    if (input.action === "approve") {
      if (approvalStep && workflow.current_step === approvalStep) advanceWorkflow(workflow, approvalStep);
    }
    workflowRuns = workflowRuns.map((item) => item.id === workflow.id ? workflow : item);
    addAudit(`WORKFLOW_ARTIFACT_${status}`, "workflow_artifact", id, `${status} ${existing.type} artifact in the local workflow.`);
    return structuredClone(updated);
  },
  async bulkReviewProposals(input) {
    await wait(200);
    const targets = proposals.filter(
      (item) =>
        item.dataset_id === input.dataset_id &&
        (input.pending_only === false || ["PROPOSED", "EDITED"].includes(item.status)),
    );
    const nextStatus = input.action === "approve" ? "APPROVED" : "REJECTED";
    targets.forEach((item) => {
      item.status = nextStatus as RuleProposal["status"];
    });
    addAudit(
      input.action === "approve" ? "PROPOSAL_BULK_APPROVED" : "PROPOSAL_BULK_REJECTED",
      "dataset",
      input.dataset_id,
      `${targets.length} proposals decided in bulk.`,
    );
    return structuredClone(proposals.filter((item) => item.dataset_id === input.dataset_id));
  },
  async confirmSemanticContract(id: string, input: SemanticContractConfirmInput) {
    await wait(180);
    const workflow = workflowRuns.find((item) => item.id === id);
    if (!workflow) throw new Error("Workflow run not found.");
    const existing = workflowArtifacts.find((item) => item.id === input.artifact_id);
    if (!existing) throw new Error("Semantic contract artifact not found.");
    if (existing.version !== input.expected_version) {
      throw new Error("The contract changed since it was loaded. Reload before confirming.");
    }
    const updated: AgentArtifact = { ...existing, status: "APPROVED", payload: input.contract };
    workflowArtifacts = workflowArtifacts.map((item) => (item.id === existing.id ? updated : item));
    workflowRuns = workflowRuns.map((item) => (item.id === workflow.id ? workflow : item));
    addAudit("SEMANTIC_CONTRACT_CONFIRMED", "workflow_artifact", existing.id, "Confirmed the inferred semantic contract.");
    return { workflow: structuredClone(workflow), artifact: structuredClone(updated) };
  },
  async continueLoop(id: string, input: LoopDecisionInput) {
    await wait(180);
    const workflow = workflowRuns.find((item) => item.id === id);
    if (!workflow || workflow.current_step !== "ANALYZE_IMPROVE") throw new Error("The workflow is not waiting for a loop decision.");
    if (input.action === "continue") {
      if (workflow.iteration >= workflow.max_iterations) throw new Error("The maximum number of loop iterations has been reached.");
      workflow.iteration += 1;
      workflow.steps = workflow.steps.map((item) => item.key === "ANALYZE_IMPROVE" ? { ...item, status: "COMPLETED", completed_at: now() } : item);
      workflow.current_step = "REVIEW_RULES";
      workflow.steps = workflow.steps.map((item) => item.key === "REVIEW_RULES" ? { ...item, status: "READY" } : item);
    } else {
      workflow.steps = workflow.steps.map((item) => item.key === "ANALYZE_IMPROVE" ? { ...item, status: "COMPLETED", completed_at: now() } : item);
    }
    workflowRuns = workflowRuns.map((item) => item.id === id ? workflow : item);
    addAudit("LOOP_DECISION", "workflow", id, `${input.action} loop at iteration ${workflow.iteration}.`);
    return structuredClone(workflow);
  },
  async rewindWorkflow(id: string, targetStep: WorkflowStepKey) {
    await wait(180);
    const workflow = workflowRuns.find((item) => item.id === id);
    if (!workflow) throw new Error("Workflow run not found.");
    const targetIndex = workflow.steps.findIndex((item) => item.key === targetStep);
    if (targetIndex < 0) throw new Error("Target workflow step not found.");
    const currentIndex = workflow.steps.findIndex((item) => item.key === workflow.current_step);
    if (targetIndex === currentIndex) return structuredClone(workflow);
    const target = workflow.steps[targetIndex];
    if (target.temporary) {
      workflow.current_step = targetStep;
      workflowRuns = workflowRuns.map((item) => item.id === id ? workflow : item);
      addAudit("WORKFLOW_SESSION_VIEWED", "workflow", id, `Viewed preserved ${targetStep} session.`);
      return structuredClone(workflow);
    }
    if (targetIndex < currentIndex && !["COMPLETED", "READY"].includes(target.status)) throw new Error("This workflow stage is not available for navigation.");
    const temporaryArtifactIds = new Set(workflow.steps.slice(targetIndex + 1).flatMap((item) => item.artifact_ids));
    workflow.steps = workflow.steps.map((item, index) => index === targetIndex
      ? { ...item, temporary: false, blocker: undefined }
      : index > targetIndex
        ? { ...item, temporary: true, blocker: undefined }
        : item);
    workflowArtifacts = workflowArtifacts.map((artifact) => temporaryArtifactIds.has(artifact.id) ? { ...artifact, temporary: true } : artifact);
    workflow.current_step = targetStep;
    workflowRuns = workflowRuns.map((item) => item.id === id ? workflow : item);
    addAudit("WORKFLOW_REWOUND", "workflow", id, `Returned to ${targetStep}; downstream sessions were kept temporarily.`);
    return structuredClone(workflow);
  },
  async getGraphCatalog() {
    return structuredClone(mockGraphCatalog);
  },
  async listNodeRuns(filter) {
    const key = filter.graphKey;
    const datasetFilter = filter.datasetId;
    return mockNodeRuns.filter((run) => (key ? run.graph_key === key : true)).filter((run) => !datasetFilter || run.dataset_id === datasetFilter).map((run) => structuredClone(run));
  },
  async getNodeRun(nodeRunId) {
    const run = mockNodeRuns.find((item) => item.id === nodeRunId);
    if (!run) throw new Error("Node run not found.");
    return {
      ...structuredClone(run),
      input_summary: { dataset_id: run.dataset_id, columns: { type: "list", count: 18 } },
      output_summary: { status: run.status, records: { type: "records", count: 12, fields: ["name", "role"] } },
    };
  },
  async getStewardReport(runId) {
    return {
      run_id: runId,
      filename: `steward_report_mock_${runId}.md`,
      generated_at: new Date().toISOString(),
      content: `# Steward report (mock)\n\nNo real Graph 3 run is attached in mock mode.\n`,
    };
  },
};

/* Mock graph topology — a trimmed mirror of src/agents/graph_catalog.py so the
 * offline demo renders the same shapes the live backend returns. */
const mockNode = (
  name: string,
  labelEn: string,
  labelVi: string,
  kind: NodeKind,
  purposeEn?: string,
  purposeVi?: string,
): GraphNodeSpec => ({
  name,
  label_en: labelEn,
  label_vi: labelVi,
  kind,
  purpose_en: purposeEn ?? `${labelEn} step of the pipeline.`,
  purpose_vi: purposeVi ?? `Bước ${labelVi} trong pipeline.`,
  inputs: [],
  outputs: [],
  db_tables: [],
  source: "",
});

const mockGraphCatalog: GraphCatalog = {
  graphs: [
    {
      key: "G1A",
      builder: "build_understanding_graph",
      label_en: "Graph 1A · Dataset understanding",
      label_vi: "Graph 1A · Hiểu ngữ nghĩa dữ liệu",
      run_en: "Run 1 — Proposal engine", run_vi: "Run 1 — Bộ máy đề xuất",
      summary_en: "Turns persisted profile evidence into a draft semantic contract, then stops for steward review.",
      summary_vi: "Biến hồ sơ dữ liệu đã lưu thành hợp đồng ngữ nghĩa nháp, rồi dừng chờ Steward duyệt.",
      nodes: [
        mockNode(
          "build_profile_digest",
          "Profile digest",
          "Nén hồ sơ",
          "DETERMINISTIC",
          "Compress raw profile statistics into a compact JSON digest.",
          "Nén số liệu hồ sơ thô thành JSON digest tinh gọn.",
        ),
        mockNode(
          "data_dictionary_generator",
          "Data dictionary",
          "Sinh từ điển dữ liệu",
          "LLM",
          "Normalise field names and descriptions into a data dictionary.",
          "Chuẩn hoá tên và mô tả trường theo Data Dictionary.",
        ),
        mockNode(
          "dataset_understanding",
          "Dataset understanding",
          "Hiểu tập dữ liệu",
          "LLM",
          "Infer business role and semantic type of every column.",
          "Suy luận vai trò nghiệp vụ và kiểu ngữ nghĩa của từng cột.",
        ),
      ],
      edges: [
        {
          from: "build_profile_digest",
          to: "data_dictionary_generator",
          condition: "no dictionary supplied",
          condition_en: "no dictionary supplied",
          condition_vi: "chưa có từ điển",
        },
        {
          from: "build_profile_digest",
          to: "dataset_understanding",
          condition: "dictionary already supplied",
          condition_en: "dictionary already supplied",
          condition_vi: "đã có từ điển",
        },
        { from: "data_dictionary_generator", to: "dataset_understanding" },
        { from: "dataset_understanding", to: "END" },
      ],
    },
    {
      key: "G1B",
      builder: "build_rule_proposal_graph",
      label_en: "Graph 1B · Rule proposal",
      label_vi: "Graph 1B · Sinh đề xuất luật",
      run_en: "Run 1", run_vi: "Run 1",
      summary_en: "Candidates, a tailored prompt, then typed rules.",
      summary_vi: "Ứng viên, prompt riêng, rồi luật có kiểu.",
      nodes: [
        mockNode("rule_candidate_builder", "Rule candidates", "Sinh ứng viên luật", "DETERMINISTIC"),
        mockNode("prompt_customizer", "Prompt customizer", "Tuỳ biến prompt", "LLM"),
        mockNode("rule_proposer", "Rule proposer", "Đề xuất luật", "LLM"),
      ],
      edges: [
        { from: "rule_candidate_builder", to: "prompt_customizer" },
        { from: "prompt_customizer", to: "rule_proposer" },
        { from: "rule_proposer", to: "END" },
      ],
    },
    {
      key: "G2",
      builder: "build_execution_graph",
      label_en: "Graph 2 · Deterministic execution",
      label_vi: "Graph 2 · Thực thi tất định",
      run_en: "Run 2", run_vi: "Run 2",
      summary_en: "Compile to dbt, gate on validation, run, store.",
      summary_vi: "Biên dịch dbt, chặn nếu sai, chạy, lưu.",
      nodes: [
        mockNode("test_generator", "Test generator", "Sinh test", "DETERMINISTIC"),
        mockNode("validate_dbt_project", "Validate dbt project", "Kiểm định dbt", "DETERMINISTIC"),
        mockNode("dbt_validation_failed", "Validation failed", "Kiểm định thất bại", "DETERMINISTIC"),
        mockNode("test_runner", "Test runner", "Chạy test", "DETERMINISTIC"),
        mockNode("persist_report", "Persist report", "Lưu kết quả", "DETERMINISTIC"),
      ],
      edges: [
        { from: "test_generator", to: "validate_dbt_project" },
        { from: "validate_dbt_project", to: "test_runner", condition: "valid" },
        { from: "validate_dbt_project", to: "dbt_validation_failed", condition: "invalid" },
        { from: "test_runner", to: "persist_report" },
        { from: "persist_report", to: "END" },
      ],
    },
    {
      key: "G3",
      builder: "build_anomaly_graph",
      label_en: "Graph 3 · Anomaly & root cause",
      label_vi: "Graph 3 · Bất thường & nguyên nhân gốc",
      run_en: "Run 3", run_vi: "Run 3",
      summary_en: "Detect, investigate, persist, report.",
      summary_vi: "Phát hiện, điều tra, lưu, báo cáo.",
      nodes: [
        mockNode("anomaly_detector", "Anomaly detector", "Phát hiện bất thường", "DETERMINISTIC"),
        mockNode("hypothesis_agent", "Hypothesis agent", "Agent giả thuyết", "LLM"),
        mockNode("persist_analysis", "Persist analysis", "Lưu phân tích", "DETERMINISTIC"),
        mockNode("report_writer", "Report writer", "Viết báo cáo", "LLM"),
      ],
      edges: [
        { from: "anomaly_detector", to: "hypothesis_agent" },
        { from: "hypothesis_agent", to: "persist_analysis" },
        { from: "persist_analysis", to: "report_writer" },
        { from: "report_writer", to: "END" },
      ],
    },
  ],
  step_graphs: {
    UNDERSTAND_DATA: ["G1A"],
    PROPOSE_RULES: ["G1B"],
    RUN_CHECKS: ["G2"],
    ANALYZE_REPORT: ["G3"],
  },
  totals: { graphs: 4, nodes: 15, llm_nodes: 6, deterministic_nodes: 9, gate_nodes: 0 },
};

const mockNodeRun = (
  id: string,
  graphKey: GraphKey,
  nodeName: string,
  kind: NodeKind,
  sequence: number,
  status: NodeRunStatus,
  durationMs: number,
): NodeRun => ({
  id,
  graph_run_id: `gr-mock-${graphKey}`,
  graph_key: graphKey,
  node_name: nodeName,
  node_kind: kind,
  sequence,
  status,
  started_at: new Date(Date.now() - durationMs).toISOString(),
  completed_at: new Date().toISOString(),
  duration_ms: durationMs,
  error_message: status === "FAILED" ? "Mock failure for demonstration." : null,
  model_name: kind === "LLM" ? "gpt-4o-mini" : null,
  workflow_run_id: "wf-mock",
  dataset_id: datasetId,
  dq_run_id: graphKey === "G2" || graphKey === "G3" ? "dq-mock" : null,
  anomaly_run_id: graphKey === "G3" ? "anom-mock" : null,
});

const mockNodeRuns: NodeRun[] = [
  mockNodeRun("nr-1", "G1A", "build_profile_digest", "DETERMINISTIC", 1, "SUCCEEDED", 240),
  mockNodeRun("nr-2", "G1A", "data_dictionary_generator", "LLM", 2, "SUCCEEDED", 4100),
  mockNodeRun("nr-3", "G1A", "dataset_understanding", "LLM", 3, "SUCCEEDED", 9700),
  mockNodeRun("nr-4", "G1B", "rule_candidate_builder", "DETERMINISTIC", 1, "SUCCEEDED", 180),
  mockNodeRun("nr-5", "G1B", "prompt_customizer", "LLM", 2, "SUCCEEDED", 3600),
  mockNodeRun("nr-6", "G1B", "rule_proposer", "LLM", 3, "SUCCEEDED", 12400),
  mockNodeRun("nr-7", "G2", "test_generator", "DETERMINISTIC", 1, "SUCCEEDED", 620),
  mockNodeRun("nr-8", "G2", "validate_dbt_project", "DETERMINISTIC", 2, "SUCCEEDED", 310),
  mockNodeRun("nr-9", "G2", "test_runner", "DETERMINISTIC", 3, "SUCCEEDED", 8800),
  mockNodeRun("nr-10", "G2", "persist_report", "DETERMINISTIC", 4, "SUCCEEDED", 450),
  mockNodeRun("nr-11", "G3", "anomaly_detector", "DETERMINISTIC", 1, "SUCCEEDED", 700),
  mockNodeRun("nr-12", "G3", "hypothesis_agent", "LLM", 2, "FAILED", 1200),
];
