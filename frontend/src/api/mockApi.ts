import type {
  ApiClient,
  AuditLog,
  CreateJobResponse,
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
    rule: {
      type: "accepted_values",
      column: "payment_type",
      allowed_values: ["Flex Fare trip", "Credit card", "Cash", "No charge", "Dispute", "Unknown", "Voided trip"],
    },
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
let anomalies = new Map<string, DqAnomaly[]>();
let auditLogs: AuditLog[] = [];
let configurations: RuleConfiguration[] = [];
let currentUsername = "";
let currentRole = "USER";
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
      : ["Claiming approved rule set…", "Compiling read-only checks…", "Executing bounded queries…", "Persisting results…"];

  for (let index = 0; index < progressMessages.length; index += 1) {
    await wait(420);
    jobs = jobs.map((item) => item.id === jobId ? { ...item, status: "RUNNING", progress: Math.round(((index + 1) / progressMessages.length) * 100), message: progressMessages[index], updated_at: now() } : item);
  }
  jobs = jobs.map((item) => item.id === jobId ? { ...item, status: "SUCCEEDED", progress: 100, message: "Completed", updated_at: now() } : item);
}

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
  async createManualRule(id, input: ManualRuleInput) {
    if (id !== datasetId) throw new Error("Dataset not found.");
    const proposal: RuleProposal = { id: `manual-${Date.now()}`, dataset_id: datasetId, ...input, status: "PROPOSED", evidence_refs: [], evidence_summary: "Manually authored by the Data Steward; no agent evidence attached.", confidence: 1, model_name: "manual", created_at: now(), updated_at: now() };
    proposals = [...proposals, proposal];
    addAudit("MANUAL_RULE_CREATED", "rule_proposal", proposal.id, `Created manual rule “${proposal.title}”.`);
    return proposal;
  },
  async reviewProposal(id, input: ReviewInput) {
    await wait(220);
    const existing = proposals.find((proposal) => proposal.id === id);
    if (!existing) throw new Error("Proposal not found.");
    const status: RuleProposal["status"] = input.action === "approve" ? "APPROVED" : input.action === "reject" ? "REJECTED" : "EDITED";
    const updated = { ...existing, ...input, status, rule: input.rule ?? existing.rule, updated_at: now() };
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
  async queryDatasetRows(id, query: DatasetRowQuery): Promise<DatasetRowsResponse> {
    await wait(160);
    if (id !== datasetId) throw new Error("Dataset not found.");
    let filtered = mockRows.filter((row) => {
      if (query.vendor_id && row.vendor_id !== query.vendor_id) return false;
      if (query.payment_type && row.payment_type !== query.payment_type) return false;
      if (query.min_distance !== undefined && (row.trip_distance ?? 0) < query.min_distance) return false;
      if (query.max_distance !== undefined && (row.trip_distance ?? 0) > query.max_distance) return false;
      if (query.quality_status === "ISSUE" && !rowHasIssue(row)) return false;
      if (query.quality_status === "VALID" && rowHasIssue(row)) return false;
      return true;
    });
    const sortBy = query.sort_by ?? "pickup_at";
    const direction = query.sort_direction === "asc" ? 1 : -1;
    filtered = filtered.sort((left, right) => String(left[sortBy] ?? "").localeCompare(String(right[sortBy] ?? "")) * direction);
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
};
