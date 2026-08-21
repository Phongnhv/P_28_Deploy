export type JobStatus = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED_RETRYABLE" | "FAILED";
export type JobType = "INGEST_PROFILE" | "PROPOSE_RULES" | "RUN_DQ";
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
  status: Extract<JobStatus, "PENDING" | "RUNNING">;
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

export interface RuleProposal {
  id: string;
  dataset_id: string;
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
  proposal_basis?: "SCHEMA_CONSTRAINT" | "DATA_PROFILE" | "DATA_DICTIONARY" | "HISTORICAL_RULE" | "POLICY" | "MIXED";
  evidence?: Record<string, unknown>;
  parameter_provenance?: Array<Record<string, unknown>>;
  assumptions?: string[];
  confidence_breakdown?: Record<string, unknown>;
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
  status: "PASS" | "FAIL";
  checked_count: number;
  failed_count: number;
  failed_row_ids: string[];
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
  total: number;
  limit: number;
  offset: number;
  rows: DatasetRow[];
}

export interface DatasetRowQuery {
  vendor_id?: string;
  payment_type?: string;
  min_distance?: number;
  max_distance?: number;
  quality_status?: "ALL" | "VALID" | "ISSUE";
  sort_by?: "pickup_at" | "trip_distance" | "fare_amount" | "total_amount";
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
  status: "DRAFT" | "VALIDATED" | "APPROVED" | "REJECTED" | "STALE";
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

export interface ArtifactReviewInput {
  action: "approve" | "request_revision" | "reject";
  comment?: string;
}

export interface LoopDecisionInput {
  action: "continue" | "request_changes" | "stop";
  comment?: string;
}

export interface ApiClient {
  createSession(username: string, password: string): Promise<SessionResponse>;
  deleteSession(): Promise<void>;
  listDatasets(): Promise<Dataset[]>;
  startIngestion(datasetId: string, idempotencyKey: string): Promise<CreateJobResponse>;
  getJob(jobId: string): Promise<Job>;
  getProfile(datasetId: string): Promise<DatasetProfile | null>;
  startRuleProposals(datasetId: string, idempotencyKey: string): Promise<CreateJobResponse>;
  listProposals(datasetId: string): Promise<RuleProposal[]>;
  createManualRule(datasetId: string, input: ManualRuleInput): Promise<RuleProposal>;
  reviewProposal(proposalId: string, input: ReviewInput): Promise<RuleProposal>;
  deleteProposal(proposalId: string): Promise<void>;
  listRuleConfigurations(datasetId: string): Promise<RuleConfiguration[]>;
  updateRuleConfiguration(proposalId: string, input: RuleConfigurationInput): Promise<RuleConfiguration>;
  startDqRun(ruleIds: string[], idempotencyKey: string): Promise<DqRunCreateResponse>;
  getDqRun(runId: string): Promise<DqRun>;
  getDqResults(runId: string): Promise<DqResult[]>;
  getDqAnomalies(runId: string): Promise<DqAnomaly[]>;
  getLatestDqRun(datasetId: string): Promise<DqRun | null>;
  getQualityTrends(datasetId: string): Promise<QualityTrendPoint[]>;
  queryDatasetRows(datasetId: string, query: DatasetRowQuery): Promise<DatasetRowsResponse>;
  listAuditLogs(): Promise<AuditLog[]>;
  listUsers(): Promise<UserAccount[]>;
  createUser(input: UserCreateInput): Promise<UserAccount>;
  updateUser(username: string, input: UserUpdateInput): Promise<UserAccount>;
  listDatasetAccess(datasetId: string): Promise<DatasetAccess[]>;
  grantDatasetAccess(datasetId: string, username: string, accessLevel: DatasetAccessLevel): Promise<DatasetAccess>;
  revokeDatasetAccess(datasetId: string, username: string): Promise<void>;
  createWorkflow(datasetId: string): Promise<WorkflowRun>;
  getWorkflow(workflowRunId: string): Promise<WorkflowRun>;
  runWorkflowStep(workflowRunId: string, step: WorkflowStepKey): Promise<CreateJobResponse>;
  advanceWorkflowStep(workflowRunId: string): Promise<WorkflowRun>;
  listWorkflowArtifacts(workflowRunId: string): Promise<AgentArtifact[]>;
  reviewArtifact(artifactId: string, input: ArtifactReviewInput): Promise<AgentArtifact>;
  continueLoop(workflowRunId: string, input: LoopDecisionInput): Promise<WorkflowRun>;
  rewindWorkflow(workflowRunId: string, targetStep: WorkflowStepKey): Promise<WorkflowRun>;
}
