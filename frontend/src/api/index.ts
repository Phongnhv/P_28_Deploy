import { realApiClient } from "./client";
import { mockApi } from "./mockApi";

export const isMockMode = import.meta.env.VITE_USE_MOCK_API !== "false";
export const api = isMockMode ? mockApi : realApiClient;
// Workflow contracts are still being implemented on the backend. Keep the
// stepper usable in connected-api mode through the deterministic local adapter.
export const workflowApi = mockApi;
