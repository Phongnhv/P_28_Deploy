import type {
  ApiClient,
  AuditLog,
  CreateJobResponse,
  Dataset,
  DatasetProfile,
  DqResult,
  DqRun,
  DqRunCreateResponse,
  Job,
  ReviewInput,
  RuleProposal,
  SessionResponse,
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
    description: "Flag trips where trip_distance is below zero. The aggregate profile shows a small negative-value population.",
    severity: "HIGH",
    status: "PROPOSED",
    rule: { type: "numeric_range", column: "trip_distance", min_value: 0 },
    evidence_refs: ["profile.trip_distance.negative_rate"],
    evidence_summary: "0.62% of 50,000 rows have trip_distance < 0.",
    confidence: 0.97,
    model_name: "approved-model",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "proposal-null",
    dataset_id: datasetId,
    title: "Pickup timestamp is required",
    description: "Require a pickup timestamp so each trip can be placed in a time window.",
    severity: "MEDIUM",
    status: "PROPOSED",
    rule: { type: "not_null", column: "tpep_pickup_datetime" },
    evidence_refs: ["profile.row_count"],
    evidence_summary: "Pickup timestamp completeness is below the dataset contract threshold.",
    confidence: 0.9,
    model_name: "approved-model",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "proposal-values",
    dataset_id: datasetId,
    title: "Payment type uses the approved vocabulary",
    description: "Reject payment categories that are not in the registered dataset vocabulary.",
    severity: "MEDIUM",
    status: "PROPOSED",
    rule: { type: "accepted_values", column: "payment_type", allowed_values: ["1", "2", "3", "4", "5", "6"] },
    evidence_refs: ["profile.payment_type.invalid_rate"],
    evidence_summary: "0.28% of rows contain an unrecognized payment category.",
    confidence: 0.94,
    model_name: "approved-model",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "proposal-chronology",
    dataset_id: datasetId,
    title: "Dropoff occurs after pickup",
    description: "Require the dropoff timestamp to be later than the pickup timestamp.",
    severity: "HIGH",
    status: "PROPOSED",
    rule: { type: "cross_field_comparison", columns: ["tpep_pickup_datetime", "tpep_dropoff_datetime"], operator: "<" },
    evidence_refs: ["profile.dropoff_before_pickup_rate"],
    evidence_summary: "0.19% of rows have a dropoff timestamp before pickup.",
    confidence: 0.96,
    model_name: "approved-model",
    created_at: now(),
    updated_at: now(),
  },
  {
    id: "proposal-duplicate",
    dataset_id: datasetId,
    title: "Trip fingerprint should be unique",
    description: "Flag repeated business fingerprints across pickup, dropoff and distance fields.",
    severity: "LOW",
    status: "PROPOSED",
    rule: { type: "duplicate_fingerprint", fingerprint_columns: ["tpep_pickup_datetime", "tpep_dropoff_datetime", "trip_distance"] },
    evidence_refs: ["profile.duplicate_fingerprint_rate"],
    evidence_summary: "Duplicate fingerprint rate is 0.42% across the registered artifact.",
    confidence: 0.88,
    model_name: "approved-model",
    created_at: now(),
    updated_at: now(),
  },
];

let proposals = makeProposals();
let jobs: Job[] = [];
let runs: DqRun[] = [];
let results = new Map<string, DqResult[]>();
let auditLogs: AuditLog[] = [];

function addAudit(action: string, entityType: string, entityId: string, summary: string) {
  auditLogs = [{ id: `audit-${Date.now()}`, action, entity_type: entityType, entity_id: entityId, actor: "Data Steward", summary, created_at: now() }, ...auditLogs];
}

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
      : ["Claiming approved rule set…", "Compiling read-only checks…", "Executing bounded queries…", "Persisting results…"];

  for (let index = 0; index < progressMessages.length; index += 1) {
    await wait(420);
    jobs = jobs.map((item) => item.id === jobId ? { ...item, status: "RUNNING", progress: Math.round(((index + 1) / progressMessages.length) * 100), message: progressMessages[index], updated_at: now() } : item);
  }
  jobs = jobs.map((item) => item.id === jobId ? { ...item, status: "SUCCEEDED", progress: 100, message: "Completed", updated_at: now() } : item);
}

export const mockApi: ApiClient = {
  async createSession(password): Promise<SessionResponse> {
    await wait(300);
    if (password.trim().length < 4) throw new Error("Use the local demo password: demo");
    addAudit("SESSION_STARTED", "session", "local-session", "Started a local Data Steward session.");
    return { csrf_token: "local-csrf-token", expires_at: new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString() };
  },
  async deleteSession() {
    await wait(150);
    addAudit("SESSION_ENDED", "session", "local-session", "Ended the local Data Steward session.");
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
    return id === datasetId && dataset.status === "PROFILE_READY" ? profile : null;
  },
  async startRuleProposals(id, _idempotencyKey) {
    if (id !== datasetId || dataset.status !== "PROFILE_READY") throw new Error("A completed profile is required before requesting proposals.");
    const job = makeJob("PROPOSE_RULES");
    void finishJob(job.id, job.type).then(() => addAudit("PROPOSALS_CREATED", "dataset", datasetId, "Generated typed proposals from aggregate evidence."));
    return { job_id: job.id, status: "PENDING" } satisfies CreateJobResponse;
  },
  async listProposals(id) {
    await wait(200);
    return id === datasetId ? proposals : [];
  },
  async reviewProposal(id, input: ReviewInput) {
    await wait(220);
    const existing = proposals.find((proposal) => proposal.id === id);
    if (!existing) throw new Error("Proposal not found.");
    const status: RuleProposal["status"] = input.action === "approve" ? "APPROVED" : input.action === "reject" ? "REJECTED" : "EDITED";
    const updated = { ...existing, ...input, status, rule: input.rule ?? existing.rule, updated_at: now() };
    delete (updated as Partial<ReviewInput>).action;
    proposals = proposals.map((proposal) => proposal.id === id ? updated : proposal);
    addAudit(`PROPOSAL_${status}`, "rule_proposal", id, `${status === "APPROVED" ? "Approved" : status === "REJECTED" ? "Rejected" : "Edited"} rule proposal “${updated.title}”.`);
    return updated;
  },
  async startDqRun(ruleIds, _idempotencyKey) {
    const approved = proposals.filter((proposal) => ruleIds.includes(proposal.id) && proposal.status === "APPROVED");
    if (!approved.length) throw new Error("At least one approved rule is required.");
    const job = makeJob("RUN_DQ");
    const run: DqRun = { id: `run-${Date.now()}`, job_id: job.id, dataset_id: datasetId, rule_ids: approved.map((proposal) => proposal.id), status: "PENDING", total_failed: 0, total_checked: 0, created_at: now() };
    runs = [...runs, run];
    void finishJob(job.id, job.type).then(() => {
      const runResults = approved.map((proposal, index): DqResult => ({ rule_id: proposal.id, rule_title: proposal.title, status: index === 1 ? "PASS" : "FAIL", checked_count: 50000, failed_count: index === 1 ? 0 : index === 0 ? 310 : 140, failed_row_ids: index === 1 ? [] : [`row-${1000 + index}`, `row-${2000 + index}`, `row-${3000 + index}`] }));
      results.set(run.id, runResults);
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
  async listAuditLogs() {
    await wait(180);
    return auditLogs;
  },
};
