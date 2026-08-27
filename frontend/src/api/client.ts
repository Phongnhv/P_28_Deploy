import type {
  ApiClient,
  AuditLog,
  CreateJobResponse,
  Dataset,
  DatasetImportResponse,
  DatasetProfile,
  DqRunCreateResponse,
  DqResult,
  DqAnomaly,
  DatasetRowQuery,
  DatasetRowsResponse,
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
  Graph1Run,
  Graph1NodeExecution,
  Graph1RuleDecision,
  AnalysisRun,
  AnalysisNodeExecution,
  AnalysisResult,
} from "../types";

function resolveApiBaseUrl(configuredValue: string) {
  const configured = configuredValue.replace(/\/$/, "");
  if (!configured || typeof window === "undefined") return configured;
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

export const apiBaseUrl = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL ?? "");
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
  if (!apiBaseUrl) {
    throw new ApiError(503, "API_NOT_CONFIGURED", "VITE_API_BASE_URL is not configured.");
  }

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

async function requestWithTransientRetry<T>(path: string, options: RequestInit): Promise<T> {
  try {
    return await request<T>(path, options);
  } catch (error) {
    // A workflow-step request supplies an idempotency key, so one short retry
    // is safe when the local API briefly drops a TCP connection while starting
    // or recovering. HTTP errors are deliberately not retried.
    if (!(error instanceof TypeError)) throw error;
    await new Promise((resolve) => window.setTimeout(resolve, 500));
    try {
      return await request<T>(path, options);
    } catch (retryError) {
      if (retryError instanceof TypeError) {
        throw new ApiError(503, "API_UNREACHABLE", "Cannot reach the API service. Confirm that the local backend is running, then try again.");
      }
      throw retryError;
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
    }>(`/api/v1/workspaces/${encodeURIComponent(workspaceId)}/datasets/import`, {
      method: "POST",
      headers: { "Idempotency-Key": `ui-${checksum}` },
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
    } satisfies DatasetImportResponse;
  },
  async deleteDataset(id) {
    await request<void>(`/api/v1/datasets/${id}`, { method: "DELETE" });
  },
  async startIngestion(datasetId, idempotencyKey) {
    return request<CreateJobResponse>(`/api/v1/datasets/${datasetId}/ingestions`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    });
  },
  getJob(jobId) {
    return request<Job>(`/api/v1/jobs/${jobId}`);
  },
  getProfile(datasetId) {
    return request<DatasetProfile | null>(`/api/v1/datasets/${datasetId}/profile`);
  },
  async startRuleProposals(datasetId, idempotencyKey) {
    return request<CreateJobResponse>(`/api/v1/datasets/${datasetId}/rule-proposals`, {
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
  listRuleConfigurations(datasetId) {
    return request<RuleConfiguration[]>(`/api/v1/rule-configurations?dataset_id=${encodeURIComponent(datasetId)}`);
  },
  updateRuleConfiguration(proposalId, input: RuleConfigurationInput) {
    return request<RuleConfiguration>(`/api/v1/rule-proposals/${encodeURIComponent(proposalId)}/configuration`, {
      method: "PATCH", body: JSON.stringify(input),
    });
  },
  async startDqRun(ruleIds, idempotencyKey) {
    return request<DqRunCreateResponse>("/api/v1/dq-runs", {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ rule_ids: ruleIds }),
    });
  },
  getDqRun(runId) {
    return request<DqRun>(`/api/v1/dq-runs/${runId}`);
  },
  getDqResults(runId) {
    return request<DqResult[]>(`/api/v1/dq-runs/${runId}/results`);
  },
  getDqAnomalies(runId) {
    return request<DqAnomaly[]>(`/api/v1/dq-runs/${runId}/anomalies`);
  },
  getLatestDqRun(datasetId) {
    return request<DqRun | null>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/dq-runs/latest`);
  },
  getQualityTrends(datasetId) {
    return request<QualityTrendPoint[]>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/quality-trends`);
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
  createWorkflow(datasetId: string, fresh = false) {
    const query = fresh ? "?fresh=true" : "";
    return request<WorkflowRun>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/workflows${query}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },
  getWorkflow(workflowRunId: string) {
    return request<WorkflowRun>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}`);
  },
  runWorkflowStep(workflowRunId: string, step: WorkflowStepKey) {
    const idempotencyKey = crypto.randomUUID();
    return requestWithTransientRetry<CreateJobResponse>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}/steps/${step}`, {
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
  createGraph1Run(datasetId: string, datasetVersionId?: string, profileRunId?: string) {
    const query = new URLSearchParams();
    if (datasetVersionId) query.set("dataset_version_id", datasetVersionId);
    if (profileRunId) query.set("profile_run_id", profileRunId);
    const suffix = query.toString() ? `?${query.toString()}` : "";
    return request<Graph1Run>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/graph1-runs${suffix}`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
    });
  },
  getLatestGraph1Run(datasetId: string, datasetVersionId?: string) {
    const query = datasetVersionId ? `?dataset_version_id=${encodeURIComponent(datasetVersionId)}` : "";
    return request<Graph1Run | null>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/graph1-runs/latest${query}`);
  },
  getGraph1Run(runId: string) {
    return request<Graph1Run>(`/api/v1/graph1-runs/${encodeURIComponent(runId)}`);
  },
  listGraph1Nodes(runId: string) {
    return request<Graph1NodeExecution[]>(`/api/v1/graph1-runs/${encodeURIComponent(runId)}/nodes`);
  },
  confirmGraph1Semantic(runId: string, contract: Record<string, unknown>) {
    return request<Graph1Run>(`/api/v1/graph1-runs/${encodeURIComponent(runId)}/semantic-review`, {
      method: "POST", body: JSON.stringify({ contract }),
    });
  },
  reviewGraph1Rules(runId: string, decisions: Graph1RuleDecision[]) {
    return request<Graph1Run>(`/api/v1/graph1-runs/${encodeURIComponent(runId)}/rule-review`, {
      method: "POST", body: JSON.stringify({ decisions }),
    });
  },
  createAnalysisRun(graph1RunId: string, rerun = false) {
    // The analysis launch is idempotent by Graph 1 run. Retry a dropped
    // connection so a committed run can still be recovered by its key.
    const query = rerun ? "?rerun=true" : "";
    return requestWithTransientRetry<AnalysisRun>(`/api/v1/graph1-runs/${encodeURIComponent(graph1RunId)}/analysis-runs${query}`, {
      method: "POST",
      headers: { "Idempotency-Key": rerun ? `analysis-rerun-${graph1RunId}-${crypto.randomUUID()}` : `analysis-${graph1RunId}` },
    });
  },
  getAnalysisRun(analysisRunId: string) {
    return request<AnalysisRun>(`/api/v1/analysis-runs/${encodeURIComponent(analysisRunId)}`);
  },
  listAnalysisNodes(analysisRunId: string) {
    return request<AnalysisNodeExecution[]>(`/api/v1/analysis-runs/${encodeURIComponent(analysisRunId)}/nodes`);
  },
  getAnalysisResult(analysisRunId: string) {
    return request<AnalysisResult>(`/api/v1/analysis-runs/${encodeURIComponent(analysisRunId)}/result`);
  },
};
