import type {
  ApiClient,
  AuditLog,
  CreateJobResponse,
  Dataset,
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

const apiBaseUrl = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL ?? "");
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
  if (options.body) headers.set("Content-Type", "application/json");
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
      | { code?: string; message?: string }
      | null;
    throw new ApiError(
      response.status,
      payload?.code ?? "API_ERROR",
      payload?.message ?? `Request failed with status ${response.status}.`,
    );
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
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
  listProposals(datasetId) {
    return request<RuleProposal[]>(`/api/v1/rule-proposals?dataset_id=${encodeURIComponent(datasetId)}`);
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
  createWorkflow(datasetId: string) {
    return request<WorkflowRun>(`/api/v1/datasets/${encodeURIComponent(datasetId)}/workflows`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },
  getWorkflow(workflowRunId: string) {
    return request<WorkflowRun>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}`);
  },
  runWorkflowStep(workflowRunId: string, step: WorkflowStepKey) {
    return request<CreateJobResponse>(`/api/v1/workflows/${encodeURIComponent(workflowRunId)}/steps/${step}`, {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
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
};
