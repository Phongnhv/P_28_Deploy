import type {
  ApiClient,
  AuditLog,
  CreateJobResponse,
  Dataset,
  DatasetImportResponse,
  DatasetProfile,
  DqRunCreateResponse,
  DqResult,
  ActiveRule,
  AnomalyFeedbackInput,
  AnomalyHypothesis,
  AnomalySignal,
  DqAnomaly,
  DataDictionary,
  DatasetRowQuery,
  DatasetRowsResponse,
  SemanticContractConfirmInput,
  QualityTrendPoint,
  DqRun,
  Job,
  ManualRuleInput,
  ReviewInput,
  RuleProposal,
  SessionResponse,
  DatasetAccess,
  DatasetAccessLevel,
  RuleConfiguration,
  RuleConfigurationInput,
  UserAccount,
  UserCreateInput,
  UserUpdateInput,
  WorkflowRun,
  WorkflowStepKey,
  AgentArtifact,
  ArtifactReviewInput,
  LoopDecisionInput,
  GraphCatalog,
  NodeRun,
  NodeRunDetail,
  NodeRunFilter,
  StewardReport,
} from "../types";

function resolveApiBaseUrl(configuredValue?: string) {
  const configured = (configuredValue || "").replace(/\/$/, "");
  if (!configured) {
    return typeof window !== "undefined" && window.location.port === "5173"
      ? "http://localhost:8000"
      : "";
  }
  if (typeof window === "undefined") return configured;
  try {
    const url = new URL(configured);
    const loopbackHosts = new Set(["localhost", "127.0.0.1"]);
    if (loopbackHosts.has(url.hostname) && loopbackHosts.has(window.location.hostname)) {
      url.hostname = window.location.hostname;
      return url.toString().replace(/\/$/, "");
    }
  } catch {
    return configured;
  }
  return configured;
}

export const apiBaseUrl = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL);
const workspaceId = (import.meta.env.VITE_WORKSPACE_ID ?? "").trim();
const csrfStorageKey = "ridepulse.csrf";
let csrfToken = typeof window === "undefined" ? "" : window.sessionStorage.getItem(csrfStorageKey) ?? "";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function setCsrfToken(token: string) {
  csrfToken = token;
  window.sessionStorage.setItem(csrfStorageKey, token);
}

export function clearApiSession() {
  csrfToken = "";
  if (typeof window !== "undefined") window.sessionStorage.removeItem(csrfStorageKey);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (method !== "GET" && method !== "HEAD" && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as
      | { code?: string; message?: string; detail?: { code?: string; message?: string } | string }
      | null;
    const detail = payload && typeof payload.detail === "object" && payload.detail !== null
      ? payload.detail
      : null;
    throw new ApiError(
      response.status,
      detail?.code ?? payload?.code ?? "API_ERROR",
      detail?.message ?? payload?.message ?? (typeof payload?.detail === "string" ? payload.detail : undefined) ?? `Request failed with status ${response.status}.`,
    );
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestWithTransientRetry<T>(
  path: string,
  options: RequestInit = {},
  retries = 2,
  delayMs = 400,
): Promise<T> {
  const transientStatuses = new Set([408, 425, 429, 500, 502, 503, 504]);
  let attempt = 0;
  while (true) {
    try {
      return await request<T>(path, options);
    } catch (error) {
      // Cloud Run can briefly return 5xx while an instance is warming up or
      // being replaced. Treat those responses like a dropped connection so a
      // job poll does not fail on the first transient platform blip.
      const retryable =
        error instanceof TypeError ||
        (error instanceof ApiError && transientStatuses.has(error.status));
      if (!retryable || attempt >= retries) {
        if (error instanceof TypeError) {
          throw new ApiError(
            503,
            "API_UNREACHABLE",
            "Cannot reach the API service. Confirm that the local backend is running, then try again.",
          );
        }
        throw error;
      }
      attempt += 1;
      await new Promise((resolve) => window.setTimeout(resolve, delayMs * attempt));
    }
  }
}

export const realApiClient: ApiClient = {
  async createSession(username, password) {
    const result = await request<SessionResponse>("/api/v1/session", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    setCsrfToken(result.csrf_token);
    return result;
  },
  async deleteSession() {
    await request<void>("/api/v1/session", { method: "DELETE" });
    clearApiSession();
  },
  async listDatasets() {
    return request<Dataset[]>("/api/v1/datasets");
  },
  async importDataset(file) {
    if (!workspaceId) {
      throw new ApiError(503, "WORKSPACE_NOT_CONFIGURED", "VITE_WORKSPACE_ID is not configured for versioned dataset import.");
    }
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    const checksum = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
    const body = new FormData();
    body.append("file", file);
    body.append("dataset_name", file.name.replace(/\.[^.]+$/, ""));
    body.append("client_sha256", checksum);
    const payload = await request<{
      dataset: { id: string; name: string; status?: Dataset["status"] };
      version: { id: string; version_number: number; status: string; checksum: string; row_count: number };
      job: { job_id: string; status: string };
      profile_run_id?: string;
      idempotent_replay?: boolean;
    }>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/datasets/import`, {
      method: "POST",
      // Unique per upload attempt, not per file. Keying on the content alone
      // meant re-uploading the same file returned the first run's dataset and
      // never profiled anything again — deliberately re-importing a file is a
      // new run, not a duplicate submit. Computed once per call, so a transient
      // network retry still reuses the same key.
      headers: { "Idempotency-Key": `ui-${checksum}-${crypto.randomUUID()}` },
      body,
    });
    return {
      dataset: {
        id: payload.dataset.id,
        name: payload.dataset.name,
        description: "Generic versioned CSV/Parquet dataset",
        status: payload.dataset.status ?? "REGISTERED",
        row_count: payload.version.row_count,
        source_label: file.name,
        manifest_version: "versioned-v1",
        checksum: payload.version.checksum,
        updated_at: new Date().toISOString(),
        data_explorer_available: payload.version.status === "READY",
        dataset_version_id: payload.version.id,
        version_number: payload.version.version_number,
        profile_run_id: payload.profile_run_id,
      },
      job: {
        job_id: payload.job.job_id,
        status: payload.job.status === "RUNNING" ? "RUNNING" : "PENDING",
      },
      idempotent_replay: payload.idempotent_replay ?? false,
    } satisfies DatasetImportResponse;
  },
  async deleteDataset(id) {
    await request<void>(`/api/v1/datasets/${id}`, { method: "DELETE" });
  },
  async startIngestion(datasetId, idempotencyKey) {
    return requestWithTransientRetry<CreateJobResponse>(`/api/v1/datasets/${datasetId}/ingestions`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    });
  },
  getJob(jobId) {
    return requestWithTransientRetry<Job>(`/api/v1/jobs/${jobId}`);
  },
  getProfile(datasetId: string, datasetVersionId?: string, profileRunId?: string) {
    const query = new URLSearchParams();
    if (datasetVersionId) query.set("dataset_version_id", datasetVersionId);
    if (profileRunId) query.set("profile_run_id", profileRunId);
    return requestWithTransientRetry<DatasetProfile | null>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/profile?${query}`);
  },
  async startRuleProposals(datasetId, idempotencyKey) {
    return requestWithTransientRetry<CreateJobResponse>(`/api/v1/datasets/${datasetId}/rule-proposals`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    });
  },
  listProposals(datasetId, workflowRunId) {
    const query = new URLSearchParams({ dataset_id: datasetId });
    if (workflowRunId) query.set("workflow_run_id", workflowRunId);
    return request<RuleProposal[]>(`/api/v1/rule-proposals?${query.toString()}`);
  },
  createManualRule(datasetId, input: ManualRuleInput) {
    return request<RuleProposal>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/rule-proposals/manual`, { method: "POST", body: JSON.stringify(input) });
  },
  reviewProposal(proposalId, input) {
    return request<RuleProposal>(`/api/v1/rule-proposals/${proposalId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  },
  deleteProposal(proposalId) {
    return request<void>(`/api/v1/rule-proposals/${encodeURIComponent(proposalId)}`, { method: "DELETE" });
  },
  bulkReviewProposals(input) {
    return request<RuleProposal[]>("/api/v1/rule-proposals/bulk-review", {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  listRuleConfigurations(datasetId) {
    return request<RuleConfiguration[]>(`/api/v1/rule-configurations?dataset_id=${encodeURIComponent(datasetId)}`);
  },
  updateRuleConfiguration(proposalId, input: RuleConfigurationInput) {
    return request<RuleConfiguration>(`/api/v1/rule-proposals/${encodeURIComponent(proposalId)}/configuration`, {
      method: "PATCH", body: JSON.stringify(input),
    });
  },
  async startDqRun(ruleIds, idempotencyKey) {
    return requestWithTransientRetry<DqRunCreateResponse>("/api/v1/dq-runs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ rule_ids: ruleIds }),
    });
  },
  getDqRun(runId) {
    return requestWithTransientRetry<DqRun>(`/api/v1/dq-runs/${runId}`);
  },
  getDqResults(runId) {
    return requestWithTransientRetry<DqResult[]>(`/api/v1/dq-runs/${runId}/results`);
  },
  getDqAnomalies(runId) {
    return requestWithTransientRetry<DqAnomaly[]>(`/api/v1/dq-runs/${runId}/anomalies`);
  },
  async getActiveRules(datasetId) {
    const response = await request<{ total_rules: number; rules: ActiveRule[] }>(
      `/api/v1/dq/active-rules?dataset_id=${encodeURIComponent(datasetId)}`,
    );
    return response.rules;
  },
  getAnomalySignals(runId) {
    return requestWithTransientRetry<AnomalySignal[]>(
      `/api/v1/dq/anomaly-runs/${encodeURIComponent(runId)}/signals`,
    );
  },
  getAnomalyHypotheses(runId) {
    return requestWithTransientRetry<AnomalyHypothesis[]>(
      `/api/v1/dq/anomaly-runs/${encodeURIComponent(runId)}/hypotheses`,
    );
  },
  async submitAnomalyFeedback(runId, input) {
    await request<{ status: string }>(
      `/api/v1/dq/anomaly-runs/${encodeURIComponent(runId)}/feedback`,
      { method: "POST", body: JSON.stringify(input) },
    );
  },
  getLatestDqRun(datasetId, workflowRunId) {
    const scope = workflowRunId ? `?workflow_run_id=${encodeURIComponent(workflowRunId)}` : "";
    return requestWithTransientRetry<DqRun | null>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/dq-runs/latest${scope}`);
  },
  getQualityTrends(datasetId) {
    return requestWithTransientRetry<QualityTrendPoint[]>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/quality-trends`);
  },
  queryDatasetRows(datasetId, query: DatasetRowQuery) {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== "") params.set(key, String(value));
    });
    return request<DatasetRowsResponse>(
      `/api/v1/datasets/${encodeURIComponent(datasetId)}/rows?${params.toString()}`,
    );
  },
  getDataDictionary(datasetId) {
    // The API answers 200 with `null` when nothing was uploaded: that is the
    // signal for "the agent will infer it", not a missing resource.
    return request<DataDictionary | null>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/data-dictionary`);
  },
  uploadDataDictionary(datasetId, file) {
    const body = new FormData();
    body.append("file", file);
    return request<DataDictionary>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/data-dictionary`, {
      method: "POST",
      body,
    });
  },
  async deleteDataDictionary(datasetId) {
    await request<void>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/data-dictionary`, { method: "DELETE" });
  },
  listAuditLogs() {
    return request<AuditLog[]>("/api/v1/audit-logs?limit=50");
  },
  listUsers() {
    return request<UserAccount[]>("/api/v1/admin/users");
  },
  createUser(input: UserCreateInput) {
    return request<UserAccount>("/api/v1/admin/users", { method: "POST", body: JSON.stringify(input) });
  },
  updateUser(username: string, input: UserUpdateInput) {
    return request<UserAccount>(`/api/v1/admin/users/${encodeURIComponent(username)}`, { method: "PATCH", body: JSON.stringify(input) });
  },
  listDatasetAccess(datasetId: string) {
    return request<DatasetAccess[]>(`/api/v1/admin/datasets/${encodeURIComponent(datasetId)}/access`);
  },
  grantDatasetAccess(datasetId: string, username: string, accessLevel: DatasetAccessLevel) {
    return request<DatasetAccess>(`/api/v1/admin/datasets/${encodeURIComponent(datasetId)}/access/${encodeURIComponent(username)}`, {
      method: "PUT", body: JSON.stringify({ access_level: accessLevel }),
    });
  },
  revokeDatasetAccess(datasetId: string, username: string) {
    return request<void>(`/api/v1/admin/datasets/${encodeURIComponent(datasetId)}/access/${encodeURIComponent(username)}`, { method: "DELETE" });
  },
  createWorkflow(datasetId: string, fresh = false, datasetVersionId?: string, freshProfile = false, requestKey?: string) {
    const query = new URLSearchParams({ fresh: String(fresh) });
    if (datasetVersionId) query.set("dataset_version_id", datasetVersionId);
    if (freshProfile) query.set("fresh_profile", "true");
    return requestWithTransientRetry<WorkflowRun>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/workflows?${query}`, {
      method: "POST",
      headers: { "Idempotency-Key": requestKey || crypto.randomUUID() },
      body: JSON.stringify({}),
    });
  },
  getLatestWorkflow(datasetId: string) {
    return request<WorkflowRun | null>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/workflows/latest`);
  },
  getWorkflow(workflowRunId: string) {
    return request<WorkflowRun>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}`);
  },

  runWorkflowStep(workflowRunId: string, step: WorkflowStepKey, datasetId?: string, datasetVersionId?: string) {
    const idempotencyKey = crypto.randomUUID();
    const query = new URLSearchParams();
    if (datasetId) query.set("dataset_id", datasetId);
    if (datasetVersionId) query.set("dataset_version_id", datasetVersionId);
    return requestWithTransientRetry<CreateJobResponse>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}/steps/${step}?${query}`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    });
  },
  advanceWorkflowStep(workflowRunId: string) {
    return request<WorkflowRun>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}/advance`, {
      method: "POST",
    });
  },
  listWorkflowArtifacts(workflowRunId: string) {
    return request<AgentArtifact[]>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}/artifacts`);
  },
  reviewArtifact(artifactId: string, input: ArtifactReviewInput) {
    return request<AgentArtifact>(`/api/v1/workflow-artifacts/${encodeURIComponent(artifactId)}/review`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  confirmSemanticContract(workflowRunId: string, input: SemanticContractConfirmInput) {
    return request<{ workflow: WorkflowRun; artifact: AgentArtifact }>(
      `/api/v1/workflows/${encodeURIComponent(workflowRunId)}/semantic-contract/confirm`,
      { method: "POST", body: JSON.stringify(input) },
    );
  },
  continueLoop(workflowRunId: string, input: LoopDecisionInput) {
    return request<WorkflowRun>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}/loop-decision`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  },
  rewindWorkflow(workflowRunId: string, targetStep: WorkflowStepKey) {
    return request<WorkflowRun>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}/rewind`, {
      method: "POST",
      body: JSON.stringify({ target_step: targetStep }),
    });
  },
  getGraphCatalog() {
    return requestWithTransientRetry<GraphCatalog>("/api/v1/graph/catalog");
  },
  listNodeRuns(filter: NodeRunFilter) {
    const params = new URLSearchParams();
    if (filter.workflowRunId) params.set("workflow_run_id", filter.workflowRunId);
    if (filter.datasetId) params.set("dataset_id", filter.datasetId);
    if (filter.dqRunId) params.set("dq_run_id", filter.dqRunId);
    if (filter.anomalyRunId) params.set("anomaly_run_id", filter.anomalyRunId);
    if (filter.graphKey) params.set("graph_key", filter.graphKey);
    if (filter.graphRunId) params.set("graph_run_id", filter.graphRunId);
    if (filter.limit) params.set("limit", String(filter.limit));
    const query = params.toString();
    return requestWithTransientRetry<NodeRun[]>(`/api/v1/graph/node-runs${query ? `?${query}` : ""}`);
  },
  getNodeRun(nodeRunId: string) {
    return requestWithTransientRetry<NodeRunDetail>(`/api/v1/graph/node-runs/${encodeURIComponent(nodeRunId)}`);
  },
  getStewardReport(runId: string) {
    return requestWithTransientRetry<StewardReport>(`/api/v1/dq-runs/${encodeURIComponent(runId)}/steward-report`);
  },
};
