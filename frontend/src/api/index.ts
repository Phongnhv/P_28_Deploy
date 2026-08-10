import { realApiClient } from "./client";
import { mockApi } from "./mockApi";

export const isMockMode = import.meta.env.VITE_USE_MOCK_API !== "false";
export const api = isMockMode ? mockApi : realApiClient;
