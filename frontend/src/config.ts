declare global {
  interface Window {
    __APP_CONFIG__?: { API_BASE_URL?: string };
  }
}

/** Runtime override (/config.js) > build-time VITE_API_BASE_URL > local default. */
export const API_BASE_URL: string = (
  (globalThis as { __APP_CONFIG__?: Window["__APP_CONFIG__"] }).__APP_CONFIG__?.API_BASE_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  "http://localhost:8000"
).replace(/\/+$/, "");

export const REQUEST_TIMEOUT_MS = 60_000;
export const MAX_MESSAGE_CHARS = 4000;
