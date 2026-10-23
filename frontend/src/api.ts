// One door to the backend. The token is the only identity the UI holds; it never sends a role.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public rule?: string,
    public errorId?: string,
    public where?: string,
  ) {
    super(message);
  }
}

const TOKEN_KEY = "vestly.token";

export function storedToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function storeToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Private windows can refuse storage; the session then lasts as long as the page.
  }
}

type Options = { method?: string; body?: unknown; token?: string | null };

export async function api<T = any>(path: string, options: Options = {}): Promise<T> {
  const token = options.token === undefined ? storedToken() : options.token;
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method: options.method ?? (options.body === undefined ? "GET" : "POST"),
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (exc) {
    throw new ApiError(0, "network", `Could not reach the Vestly backend for ${path}: ${String(exc)}`);
  }
  const text = await response.text();
  let payload: any = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = { message: text };
  }
  if (!response.ok) {
    const detail = Array.isArray(payload?.detail) ? payload.detail.map((item: any) => item.msg).join("; ") : payload?.detail;
    throw new ApiError(
      response.status,
      payload?.error ?? "error",
      payload?.message ?? detail ?? `${response.status} on ${path}`,
      payload?.rule,
      payload?.error_id,
      payload?.where,
    );
  }
  return payload as T;
}

export const post = <T = any>(path: string, body: unknown = {}, token?: string | null) => api<T>(path, { method: "POST", body, token });

export type Person = { handle: string; display_name: string; role: string; department: string | null; permissions: string[] };

export async function signIn(handle: string): Promise<Person & { token: string }> {
  return post("/auth/login", { handle }, null);
}

/** The open month: the one before the simulated clock's today (close runs early the next month). */
export function openPeriod(today: string): string {
  const [year, month] = today.split("-").map(Number);
  const y = month === 1 ? year - 1 : year;
  const m = month === 1 ? 12 : month - 1;
  return `${y}-${String(m).padStart(2, "0")}`;
}
