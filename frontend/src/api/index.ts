import { realApiClient } from "./client";
import { mockApi } from "./mockApi";

export const isMockMode = import.meta.env.VITE_USE_MOCK_API !== "false";
export const api = isMockMode ? mockApi : realApiClient;
// The workflow has the same durable contract as the rest of the product API.
export const workflowApi = api;
