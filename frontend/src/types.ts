export type JobStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED_RETRYABLE" | "FAILED";
export type JobType = "INGEST_PROFILE" | "GRAPH1_EXECUTION" | "GRAPH1_CONTINUATION" | "ANALYSIS_GRAPH2_GRAPH3" | "UNDERSTAND_DATA" | "PROPOSE_RULES" | "RUN_DQ";
export type ProposalStatus = "PROPOSED" | "APPROVED" | "EDITED" | "REJECTED";
export type UserRole = "USER" | "STEWARD" | "ADMIN";
export type AccountStatus = "ACTIVE" | "SUSPENDED" | "DISABLED";
export type DatasetAccessLevel = "READ" | "MANAGE";
export type RuleExecutionStatus = "ACTIVE" | "PAUSED";
export type RuleScheduleFrequency = "MANUAL" | "HOURLY" | "DAILY";
export type WorkflowStepKey =
  | "UPLOAD_PROFILE"
  | "UNDERSTAND_DATA"
  | "PROPOSE_RULES"
  | "REVIEW_RULES"
  | "PUBLISH_RULESET"
  | "RUN_CHECKS"
  | "ANALYZE_REPORT"
  | "PROPOSE_CODE"
  | "REVIEW_EXECUTE"
  | "ANALYZE_IMPROVE";
export type WorkflowStepStatus =
  | "LOCKED"
  | "READY"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "COMPLETED"
  | "FAILED"
  | "STALE";
export type AgentArtifactType =
  | "SEMANTIC_CONTRACT"
  | "RULE_SET"
  | "PUBLISHED_RULESET"
  | "DQ_RUN"
  | "ANOMALY_REPORT"
  | "CODE_PROPOSAL"
  | "LOOP_RECOMMENDATION";
export type RuleType =
  | "not_null"
  | "numeric_range"
  | "accepted_values"
  | "cross_field_comparison"
  | "duplicate_fingerprint";

export interface SessionResponse {
  username: string;
  role: UserRole;
  csrf_token: string;
  expires_at: string;
}

export interface UserAccount {
  id: string;
  username: string;
  display_name: string;
  role: UserRole;
  status: AccountStatus;
  created_by?: string;
  last_login_at?: string;
  created_at: string;
  updated_at: string;
}

export interface UserCreateInput {
  username: string;
  display_name: string;
  password: string;
  role: UserRole;
}

export interface UserUpdateInput {
  display_name?: string;
  password?: string;
  role?: UserRole;
  status?: AccountStatus;
}

export interface DatasetAccess {
  id: string;
  dataset_id: string;
  username: string;
  display_name: string;
  role: UserRole;
  access_level: DatasetAccessLevel;
  granted_by: string;
  granted_at: string;
}

export interface Dataset {
  id: string;
  name: string;
  description: string;
  status: "REGISTERED" | "INGESTED" | "PROFILE_READY";
  row_count: number;
  source_label: string;
  manifest_version: string;
  checksum: string;
  updated_at: string;
  data_explorer_available?: boolean;
  dataset_version_id?: string;
  version_number?: number;
  profile_run_id?: string;
}

export interface DatasetImportResponse {
  dataset: Dataset;
  job: CreateJobResponse;
  /** Versions are content-addressed: identical bytes replay the first import
      instead of profiling again. True means nothing new was run. */
  idempotent_replay?: boolean;
}

export interface Job {
  id: string;
  type: JobType;
  status: JobStatus;
  progress: number;
  message: string;
  created_at: string;
  updated_at: string;
  error?: string;
}

/** The stable 202 response returned by create-work endpoints. */
export interface CreateJobResponse {
  job_id: string;
  status: JobStatus;
}

export interface ColumnProfile {
  name: string;
  data_type: string;
  null_rate: number;
  distinct_count: number;
  sample_value: string;
}

export interface DatasetProfile {
  dataset_id: string;
  row_count: number;
  completeness_score: number;
  validity_score: number;
  duplicate_rate: number;
  columns: ColumnProfile[];
  evidence_keys: string[];
  generated_at: string;
}

export interface RuleSpec {
  type: RuleType;
  column?: string;
  columns?: string[];
  min_value?: number;
  max_value?: number;
  allowed_values?: string[];
  operator?: string;
  fingerprint_columns?: string[];
}

/** Nguồn gốc của một đề xuất. Khớp `ProposalBasis` ở src/models/rule_schemas.py. */
export type ProposalBasis =
  | "SCHEMA_CONSTRAINT"
  | "DATA_PROFILE"
  | "DATA_DICTIONARY"
  | "HISTORICAL_RULE"
  | "POLICY"
  | "MIXED";

/**
 * Vì sao một tham số cụ thể mang giá trị đó — ví dụ `min=0` đến từ phân vị 1%
 * của hồ sơ dữ liệu, hay từ ràng buộc schema. Khớp `ParameterProvenance`.
 */
export interface ParameterProvenance {
  parameter_name: string;
  source_type: Exclude<ProposalBasis, "MIXED">;
  source_ref: string;
  derivation_method: string;
}

/**
 * Phân rã độ tin cậy thành ba thành phần kèm lời giải thích. Backend ràng buộc
 * `overall` không lệch quá 0.25 so với trung bình ba thành phần, nên hiển thị
 * cả bốn cho phép người duyệt tự kiểm chứng con số tổng.
 */
export interface ConfidenceBreakdown {
  overall: number;
  evidence_strength: number;
  business_support: number;
  sample_representativeness: number;
  explanation: string;
}

export interface RuleProposal {
  id: string;
  dataset_id: string;
  workflow_run_id?: string;
  title: string;
  description: string;
  severity: "LOW" | "MEDIUM" | "HIGH";
  status: ProposalStatus;
  rule: RuleSpec;
  evidence_refs: string[];
  evidence_summary: string;
  confidence: number;
  model_name: string;
  rule_name?: string;
  business_rationale?: string;
  proposal_basis?: ProposalBasis;
  evidence?: Record<string, unknown>;
  parameter_provenance?: ParameterProvenance[];
  assumptions?: string[];
  confidence_breakdown?: ConfidenceBreakdown;
  created_at: string;
  updated_at: string;
  source?: "AGENT" | "MANUAL";
}

export interface RuleConfiguration {
  rule_id: string;
  execution_status: RuleExecutionStatus;
  schedule_frequency: RuleScheduleFrequency;
  timezone: string;
  last_run_at?: string;
  next_run_at?: string;
  updated_at: string;
}

export interface RuleConfigurationInput {
  execution_status: RuleExecutionStatus;
  schedule_frequency: RuleScheduleFrequency;
  timezone: string;
}

export interface DqRun {
  id: string;
  job_id: string;
  dataset_id: string;
  rule_ids: string[];
  status: JobStatus;
  total_failed: number;
  total_checked: number;
  created_at: string;
  completed_at?: string;
}

/** The stable response returned when a DQ run is queued. */
export interface DqRunCreateResponse {
  job_id: string;
  run_id: string;
  status: Extract<JobStatus, "PENDING" | "RUNNING">;
}

export interface DqResult {
  rule_id: string;
  rule_title: string;
  /** SKIPPED is a rule the executor could not express, not a silent pass. */
  status: "PASS" | "FAIL" | "SKIPPED";
  checked_count: number;
  failed_count: number;
  failed_row_ids: string[];
  /** Dataset-level rules report a measured percentage instead of failing rows. */
  violation_rate?: number | null;
  error_message?: string | null;
}

export interface DqAnomaly {
  rule_id: string;
  rule_title: string;
  anomaly_type: "HIGH_VIOLATION_RATE" | "Z_SCORE_SPIKE";
  current_rate: number;
  historical_mean?: number;
  z_score?: number;
  history_size: number;
  detection_mode: "COLD_START" | "HISTORICAL";
  checked_count: number;
  failed_count: number;
  reason: string;
}

export interface DatasetRow {
  source_row_id: string;
  [key: string]: unknown;
  vendor_id?: string;
  pickup_at?: string;
  dropoff_at?: string;
  passenger_count?: number;
  trip_distance?: number;
  payment_type?: string;
  fare_amount?: number;
  total_amount?: number;
}

export interface DatasetRowsResponse {
  dataset_id: string;
  dataset_version_id?: string;
  total: number;
  limit: number;
  offset: number;
  rows: DatasetRow[];
  schema?: Array<{ name: string; logical_type?: string; physical_type?: string; nullable?: boolean }>;
}

/** Decide every proposal of a dataset in one transaction. */
export interface BulkProposalReviewInput {
  dataset_id: string;
  action: "approve" | "reject";
  /** Leave true to keep decisions the Steward already made one by one. */
  pending_only?: boolean;
}

/** Steward sign-off on the contract Graph 1A inferred. */
export interface SemanticContractConfirmInput {
  artifact_id: string;
  expected_version: number;
  contract: Record<string, unknown>;
  review_note?: string;
}

export interface DataDictionaryColumn {
  name: string;
  description: string;
  semantic_type: string;
  business_role: string;
  nullable_expected: boolean;
  governance_notes: string[];
}

export interface DataDictionaryTable {
  table_name: string;
  description: string;
  columns: DataDictionaryColumn[];
  business_rules: string[];
}

/** A Steward-supplied dictionary. `null` from the API means the agent infers one. */
export interface DataDictionary {
  id: string;
  dataset_id: string;
  dataset_version_id?: string | null;
  source: "UPLOADED" | "INFERRED";
  source_filename?: string | null;
  column_count: number;
  tables: DataDictionaryTable[];
  updated_at?: string | null;
}

export interface DatasetRowQuery {
  dataset_version_id?: string;
  vendor_id?: string;
  payment_type?: string;
  min_distance?: number;
  max_distance?: number;
  quality_status?: "ALL" | "VALID" | "ISSUE";
  filter_column?: string;
  filter_value?: string;
  sort_by?: string;
  sort_direction?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export interface QualityTrendPoint {
  run_id: string;
  created_at: string;
  quality_score: number;
  failure_rate: number;
  total_checked: number;
  total_failed: number;
  rule_count: number;
}

export interface AuditLog {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string;
  actor: string;
  summary: string;
  created_at: string;
}

export interface ReviewInput {
  action: "approve" | "reject" | "edit";
  title?: string;
  description?: string;
  severity?: RuleProposal["severity"];
  rule?: RuleSpec;
}

export interface ManualRuleInput {
  title: string;
  description: string;
  severity: RuleProposal["severity"];
  rule: RuleSpec;
}

export interface WorkflowStep {
  key: WorkflowStepKey;
  status: WorkflowStepStatus;
  artifact_ids: string[];
  /** A preserved downstream session awaiting confirmation that the upstream stage changed. */
  temporary?: boolean;
  blocker?: string;
  started_at?: string;
  completed_at?: string;
}

export interface AgentArtifact {
  id: string;
  workflow_run_id: string;
  agent_role: "DATA_RULE_AGENT" | "STANDARDIZATION_AGENT" | "LOOP_AGENT";
  type: AgentArtifactType;
  version: number;
  /** "CONFIRMED" is what the semantic-contract confirm endpoint returns. */
  status: "DRAFT" | "VALIDATED" | "APPROVED" | "CONFIRMED" | "REJECTED" | "STALE";
  /** Artifact is retained as a temporary downstream session after a rewind. */
  temporary?: boolean;
  payload: unknown;
  created_at: string;
}

export interface WorkflowRun {
  id: string;
  dataset_id: string;
  current_step: WorkflowStepKey;
  iteration: number;
  max_iterations: number;
  steps: WorkflowStep[];
}

export type Graph1RunStatus = "PENDING" | "RUNNING" | "AWAITING_SEMANTIC_REVIEW" | "AWAITING_RULE_REVIEW" | "COMPLETED" | "FAILED";
export type Graph1NodeStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "WAITING_REVIEW" | "SKIPPED";

export interface Graph1Run {
  id: string;
  dataset_id: string;
  job_id?: string | null;
  workspace_id?: string | null;
  dataset_version_id?: string | null;
  profile_run_id?: string | null;
  status: Graph1RunStatus;
  current_node?: string | null;
  error?: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface Graph1NodeExecution {
  node_key: string;
  position: number;
  status: Graph1NodeStatus;
  output: Record<string, unknown>;
  error?: string | null;
  sequence: number;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface Graph1RuleDecision {
  rule_id: string;
  action: "approve" | "reject" | "edit";
  rule?: Record<string, unknown>;
}

export type AnalysisRunStatus = "PENDING" | "RUNNING" | "PARTIAL" | "COMPLETED" | "FAILED";
export type AnalysisPhase = "PREPARING" | "GRAPH2" | "GRAPH3" | "REPORT";
export type AnalysisNodeStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "SKIPPED";

export interface AnalysisRun {
  id: string;
  job_id?: string | null;
  graph1_run_id: string;
  dataset_id: string;
  workspace_id?: string | null;
  dataset_version_id?: string | null;
  report_artifact_status?: "REGISTERED" | "UPLOAD_FAILED" | "NOT_AVAILABLE";
  report_artifact_locator?: string | null;
  status: AnalysisRunStatus;
  phase: AnalysisPhase;
  current_node?: string | null;
  test_run_id?: string | null;
  anomaly_run_id?: string | null;
  report_available: boolean;
  error?: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
}

export interface AnalysisNodeExecution {
  graph_name: "PREPARING" | "GRAPH2" | "GRAPH3" | "REPORT";
  node_key: string;
  position: number;
  status: AnalysisNodeStatus;
  output: Record<string, unknown>;
  error?: string | null;
  sequence: number;
  started_at?: string | null;
  completed_at?: string | null;
  duration_ms?: number | null;
}

export interface AnalysisAnomalyAnnotation {
  flagged: boolean;
  signal_id?: string;
  score?: number;
  reliability?: number;
  family?: string;
  explanation?: string;
}

export type AnalysisResultStatus = "PASS" | "FAIL" | "ERROR" | "SKIPPED" | "RESULT_MISMATCH";

export interface AnalysisGraph2Result {
  rule_id: string;
  rule_title: string;
  rule_type: string;
  table_name: string;
  column?: string | null;
  severity: string;
  dimension: string;
  status: AnalysisResultStatus;
  checked_count: number;
  failed_count: number;
  violation_rate: number;
  duration_ms: number;
  dbt_status: string;
  metrics_status: string;
  sample_row_ids: string[];
  evidence_refs: string[];
  error?: string | null;
  anomaly: AnalysisAnomalyAnnotation;
}

export interface AnalysisSignal {
  signal_id: string;
  family: string;
  target_type: string;
  target_id: string;
  score: number;
  reliability: number;
  observed_value?: string | null;
  baseline: Record<string, unknown>;
  sufficient_history: boolean;
  detector_name: string;
  detector_version: string;
  explanation: string;
  evidence_refs: string[];
}

export interface AnalysisHypothesis {
  id: string;
  hypothesis_type: string;
  summary: string;
  confidence: number;
  supporting_signal_ids: string[];
  contradicting_signal_ids: string[];
  evidence_refs: string[];
  recommended_checks: string[];
  missing_evidence?: string | null;
  limitations?: string | null;
  model_name: string;
  prompt_version: string;
  latency_ms: number;
  fallback_used: boolean;
}

export interface AnalysisResult {
  run: AnalysisRun;
  nodes: AnalysisNodeExecution[];
  graph2: {
    available: boolean;
    summary: { total: number; passed: number; failed: number; errors: number; skipped: number; total_checked: number; total_failed: number; duration_ms: number };
    dbt: { generated_tests_count: number; validation_status: string; validation_skipped: boolean; validation_error?: string | null; validation_attempts: number; execution_mode: string; artifact: Record<string, unknown> };
    results: AnalysisGraph2Result[];
  };
  graph3: {
    available: boolean;
    decision?: { decision: string; score: number; confidence: number; severity: string; dominant_family?: string | null; override_reason?: string | null } | null;
    signals: AnalysisSignal[];
    hypotheses: AnalysisHypothesis[];
  };
  report: { available: boolean; markdown: string; source?: "LLM" | "FALLBACK" | null; file_name?: string | null; generated_at?: string | null; artifact_status?: string; artifact?: Record<string, unknown> | null };
}

export interface ArtifactReviewInput {
  action: "approve" | "request_revision" | "reject";
  comment?: string;
}

export interface LoopDecisionInput {
  action: "continue" | "request_changes" | "stop";
  comment?: string;
}

/**
 * Một luật đang canh dữ liệu thật, sau khi được duyệt và xuất bản.
 *
 * Khác với `RuleProposal` — đề xuất chỉ là ứng viên, còn đây là thứ đang chạy.
 * Khớp `ActiveRuleResponse` ở src/models/schemas.py.
 */
export interface ActiveRule {
  rule_id: string;
  dataset_id: string;
  table_name: string;
  column?: string | null;
  rule_type: string;
  parameters: Record<string, unknown>;
  severity: string;
  dimension: string;
  rule_description: string;
  status: string;
  last_run_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/** Một tín hiệu bất thường do bộ phát hiện thống kê sinh ra. Khớp `AnomalySignalDTO`. */
export interface AnomalySignal {
  signal_id: string;
  family: string;
  target_type: string;
  target_id: string;
  score: number;
  reliability: number;
  observed_value?: string | null;
  baseline?: Record<string, unknown> | null;
  explanation_code: string;
  evidence_refs: string[];
}

/**
 * Giả thuyết nguyên nhân gốc do DeepAgent điều tra sinh ra.
 *
 * Điểm đáng chú ý là `contradicting_signal_ids`: agent được yêu cầu nêu cả bằng
 * chứng phản bác chính giả thuyết của mình, nên người đọc thấy được lập luận hai
 * chiều thay vì chỉ phần ủng hộ.
 */
export interface AnomalyHypothesis {
  id: string;
  hypothesis_type: string;
  summary: string;
  confidence: number;
  supporting_signal_ids: string[];
  contradicting_signal_ids: string[];
  evidence_refs: string[];
  recommended_checks: string[];
  missing_evidence?: string | null;
  limitations?: string | null;
  fallback_used: boolean;
}

/** Nhãn phản hồi của Steward. Khớp `AnomalyFeedbackEnum` ở models/database.py. */
export type AnomalyFeedbackLabel =
  | "TRUE_ANOMALY"
  | "FALSE_POSITIVE"
  | "EXPECTED_CHANGE"
  | "RULE_MISCONFIGURATION"
  | "UNKNOWN";

export interface AnomalyFeedbackInput {
  feedback_label: AnomalyFeedbackLabel;
  comment?: string;
}

export interface ApiClient {
  createSession(username: string, password: string): Promise<SessionResponse>;
  deleteSession(): Promise<void>;
  listDatasets(): Promise<Dataset[]>;
  importDataset(file: File): Promise<DatasetImportResponse>;
  deleteDataset(id: string): Promise<void>;
  startIngestion(datasetId: string, idempotencyKey: string): Promise<CreateJobResponse>;
  getJob(jobId: string): Promise<Job>;
  getProfile(datasetId: string): Promise<DatasetProfile | null>;
  startRuleProposals(datasetId: string, idempotencyKey: string): Promise<CreateJobResponse>;
  listProposals(datasetId: string, workflowRunId?: string): Promise<RuleProposal[]>;
  createManualRule(datasetId: string, input: ManualRuleInput): Promise<RuleProposal>;
  reviewProposal(proposalId: string, input: ReviewInput): Promise<RuleProposal>;
  deleteProposal(proposalId: string): Promise<void>;
  bulkReviewProposals(input: BulkProposalReviewInput): Promise<RuleProposal[]>;
  listRuleConfigurations(datasetId: string): Promise<RuleConfiguration[]>;
  updateRuleConfiguration(proposalId: string, input: RuleConfigurationInput): Promise<RuleConfiguration>;
  startDqRun(ruleIds: string[], idempotencyKey: string): Promise<DqRunCreateResponse>;
  getDqRun(runId: string): Promise<DqRun>;
  getDqResults(runId: string): Promise<DqResult[]>;
  getDqAnomalies(runId: string): Promise<DqAnomaly[]>;
  /** Backend nhận được cả anomaly_run_id lẫn execution_run_id, nên truyền run id là đủ. */
  /** Bộ luật đang hoạt động của một dataset — thứ đang thật sự canh dữ liệu. */
  getActiveRules(datasetId: string): Promise<ActiveRule[]>;
  getAnomalySignals(runId: string): Promise<AnomalySignal[]>;
  getAnomalyHypotheses(runId: string): Promise<AnomalyHypothesis[]>;
  submitAnomalyFeedback(runId: string, input: AnomalyFeedbackInput): Promise<void>;
  getLatestDqRun(datasetId: string): Promise<DqRun | null>;
  getQualityTrends(datasetId: string): Promise<QualityTrendPoint[]>;
  queryDatasetRows(datasetId: string, query: DatasetRowQuery): Promise<DatasetRowsResponse>;
  getDataDictionary(datasetId: string): Promise<DataDictionary | null>;
  uploadDataDictionary(datasetId: string, file: File): Promise<DataDictionary>;
  deleteDataDictionary(datasetId: string): Promise<void>;
  listAuditLogs(): Promise<AuditLog[]>;
  listUsers(): Promise<UserAccount[]>;
  createUser(input: UserCreateInput): Promise<UserAccount>;
  updateUser(username: string, input: UserUpdateInput): Promise<UserAccount>;
  listDatasetAccess(datasetId: string): Promise<DatasetAccess[]>;
  grantDatasetAccess(datasetId: string, username: string, accessLevel: DatasetAccessLevel): Promise<DatasetAccess>;
  revokeDatasetAccess(datasetId: string, username: string): Promise<void>;
  createWorkflow(datasetId: string, fresh?: boolean): Promise<WorkflowRun>;
  getWorkflow(workflowRunId: string): Promise<WorkflowRun>;
  runWorkflowStep(workflowRunId: string, step: WorkflowStepKey): Promise<CreateJobResponse>;
  advanceWorkflowStep(workflowRunId: string): Promise<WorkflowRun>;
  listWorkflowArtifacts(workflowRunId: string): Promise<AgentArtifact[]>;
  reviewArtifact(artifactId: string, input: ArtifactReviewInput): Promise<AgentArtifact>;
  confirmSemanticContract(
    workflowRunId: string,
    input: SemanticContractConfirmInput,
  ): Promise<{ workflow: WorkflowRun; artifact: AgentArtifact }>;
  continueLoop(workflowRunId: string, input: LoopDecisionInput): Promise<WorkflowRun>;
  rewindWorkflow(workflowRunId: string, targetStep: WorkflowStepKey): Promise<WorkflowRun>;
  getGraphCatalog(): Promise<GraphCatalog>;
  listNodeRuns(filter: NodeRunFilter): Promise<NodeRun[]>;
  getNodeRun(nodeRunId: string): Promise<NodeRunDetail>;
  getStewardReport(runId: string): Promise<StewardReport>;
}

/* ---------------------------------------------------------------------------
 * Graph observability
 *
 * The catalog is the static topology (what a graph *is*); node runs are the
 * telemetry (what actually happened). The UI needs both: it draws the graph
 * from the catalog even when nothing has run, then overlays run state.
 * ------------------------------------------------------------------------- */

export type GraphKey = "G1A" | "G1B" | "G1_FULL" | "G_DASHBOARD" | "G2" | "G2_DIRECT" | "G3";
export type NodeKind = "LLM" | "DETERMINISTIC" | "GATE";
export type NodeRunStatus = "RUNNING" | "SUCCEEDED" | "FAILED" | "SKIPPED";

export interface GraphNodeSpec {
  name: string;
  label_en: string;
  label_vi: string;
  kind: NodeKind;
  purpose_en: string;
  purpose_vi: string;
  inputs: string[];
  outputs: string[];
  db_tables: string[];
  source: string;
}

export interface GraphEdgeSpec {
  from: string;
  to: string;
  condition?: string;
}

export interface GraphSpec {
  key: GraphKey;
  builder: string;
  label_en: string;
  label_vi: string;
  run_en: string;
  run_vi: string;
  summary_en: string;
  summary_vi: string;
  nodes: GraphNodeSpec[];
  edges: GraphEdgeSpec[];
}

export interface GraphCatalog {
  graphs: GraphSpec[];
  step_graphs: Partial<Record<WorkflowStepKey, GraphKey[]>>;
  totals: {
    graphs: number;
    nodes: number;
    llm_nodes: number;
    deterministic_nodes: number;
    gate_nodes: number;
  };
}

export interface NodeRun {
  id: string;
  graph_run_id: string;
  graph_key: GraphKey;
  node_name: string;
  node_kind: NodeKind;
  sequence: number;
  status: NodeRunStatus;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number;
  error_message: string | null;
  model_name: string | null;
  workflow_run_id: string | null;
  dataset_id: string | null;
  dq_run_id: string | null;
  anomaly_run_id: string | null;
}

export interface NodeRunDetail extends NodeRun {
  input_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
}

export interface NodeRunFilter {
  workflowRunId?: string;
  datasetId?: string;
  dqRunId?: string;
  anomalyRunId?: string;
  graphKey?: GraphKey;
  graphRunId?: string;
  limit?: number;
}

export interface StewardReport {
  run_id: string;
  filename: string;
  generated_at: string;
  content: string;
}
