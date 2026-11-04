export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** A failed API call: `{error, detail}` from the body (docs/api.md § Errors). */
export class ApiError extends Error {
  readonly status: number;
  readonly error: string;
  readonly detail: string;

  constructor(status: number, error: string, detail: string) {
    super(detail || error || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.error = error;
    this.detail = detail;
  }
}

const unauthorizedListeners = new Set<() => void>();

/** Subscribe to 401s from any request; returns the unsubscribe function. */
export function onUnauthorized(cb: () => void): () => void {
  unauthorizedListeners.add(cb);
  return () => {
    unauthorizedListeners.delete(cb);
  };
}

/** Tell every subscriber the session is gone (also used by the event stream's session check). */
export function notifyUnauthorized(): void {
  for (const cb of [...unauthorizedListeners]) cb();
}

const setupListeners = new Set<() => void>();

/** Subscribe to 428s (the install is still in `setup`) from any request; returns the unsubscribe function. */
export function onSetupRequired(cb: () => void): () => void {
  setupListeners.add(cb);
  return () => {
    setupListeners.delete(cb);
  };
}

/** Tell every subscriber the first-run choice is pending (also used by the event stream's session check). */
export function notifySetupRequired(): void {
  for (const cb of [...setupListeners]) cb();
}

function isErrorBody(value: unknown): value is { error?: unknown; detail?: unknown } {
  return typeof value === "object" && value !== null;
}

async function toApiError(res: Response): Promise<ApiError> {
  const text = await res.text();
  let error = "";
  let detail = text;
  try {
    const parsed: unknown = JSON.parse(text);
    if (isErrorBody(parsed)) {
      error = typeof parsed.error === "string" ? parsed.error : "";
      detail = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail ?? "");
    }
  } catch {
    // not JSON: keep the raw text as the detail
  }
  return new ApiError(res.status, error, detail || res.statusText);
}

async function send(method: Method, path: string, body?: unknown): Promise<Response> {
  const init: RequestInit = { method, credentials: "same-origin" };
  if (method !== "GET") {
    // docs/api.md: every mutation carries JSON, `{}` when empty (else 415)
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body ?? {});
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    if (res.status === 401) notifyUnauthorized();
    if (res.status === 428) notifySetupRequired();
    throw await toApiError(res);
  }
  return res;
}

/** JSON request; a 204 resolves to `undefined`. */
export async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const res = await send(method, path, body);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** GET a `text/plain` resource (run logs). */
export async function requestText(path: string): Promise<string> {
  const res = await send("GET", path);
  return res.text();
}
