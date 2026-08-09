import type {
  ApiClient,
  AuditLog,
  CreateJobResponse,
  Dataset,
  DatasetProfile,
  DqRunCreateResponse,
  DqResult,
  DqRun,
  Job,
  ReviewInput,
  RuleProposal,
  SessionResponse,
} from "../types";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
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
  async createSession(password) {
    const result = await request<SessionResponse>("/api/v1/session", {
      method: "POST",
      body: JSON.stringify({ password }),
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
  reviewProposal(proposalId, input) {
    return request<RuleProposal>(`/api/v1/rule-proposals/${proposalId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
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
  listAuditLogs() {
    return request<AuditLog[]>("/api/v1/audit-logs?limit=50");
  },
};
