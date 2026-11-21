import { API_BASE_URL, REQUEST_TIMEOUT_MS } from "./config";
import type { ChatResponse, Health } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ValidationItem {
  loc?: (string | number)[];
  msg?: string;
}

/** Turn FastAPI's `detail` (a string or a list of validation errors) into one readable line. */
export function formatDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item: ValidationItem) => {
      const field = (item.loc ?? []).filter((p) => p !== "body").join(".");
      const msg = (item.msg ?? "invalid value").replace(/^Value error, /, "");
      return field ? `${field}: ${msg}` : msg;
    });
    return parts.join("; ");
  }
  return "unexpected error";
}

export function describeError(status: number, detail: unknown): string {
  const text = formatDetail(detail);
  switch (status) {
    case 422:
      return `Invalid request: ${text}`;
    case 503:
      return "All providers are unavailable right now (every API key failed). Nothing was saved. Please try again.";
    default:
      return `Server error ${status}: ${text}`;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, signal: controller.signal });
  } catch (err) {
    if (controller.signal.aborted) {
      throw new ApiError(`The request timed out after ${REQUEST_TIMEOUT_MS / 1000} s.`);
    }
    throw new ApiError(`Can't reach the server at ${API_BASE_URL}. Check that the backend is running.`);
  } finally {
    clearTimeout(timer);
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body (e.g. a proxy error page); handled below.
  }
  if (!response.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail ?? response.statusText;
    throw new ApiError(describeError(response.status, detail), response.status);
  }
  if (body === null) throw new ApiError("The server returned an unreadable response.");
  return body as T;
}

export interface SendChatOptions {
  sessionId: string;
  message: string;
  systemPrompt?: string;
  simulateFailure?: string;
}

export function sendChat({ sessionId, message, systemPrompt, simulateFailure }: SendChatOptions) {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (simulateFailure) headers["X-Simulate-Failure"] = simulateFailure;
  const payload: Record<string, string> = { session_id: sessionId, message };
  if (systemPrompt?.trim()) payload.system_prompt = systemPrompt;
  return request<ChatResponse>("/chat", { method: "POST", headers, body: JSON.stringify(payload) });
}

export function getHealth() {
  return request<Health>("/health");
}
