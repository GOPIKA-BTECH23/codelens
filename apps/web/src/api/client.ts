import type { ApiErrorBody, HealthResponse, MeResponse } from './types'

// All API calls go to the same origin under /api. Vite proxies it in dev; in production a
// Vercel rewrite does the same (docs/adr/0002-cookie-and-csrf-strategy.md). Same origin is
// what lets the session live in httpOnly cookies that this code never sees.
const API_BASE = '/api'

// Sent on every request. The API rejects state-changing requests without it (CSRF layer 2):
// a cross-site form cannot add custom headers, and a cross-site fetch that tries needs a
// CORS preflight our API refuses.
const CSRF_HEADERS = { 'X-Requested-With': 'codelens' }

// Requests that must never trigger an automatic refresh (that would loop).
const NO_REFRESH_PATHS = new Set(['/auth/refresh', '/auth/logout'])

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, {
      ...init,
      credentials: 'same-origin', // the default, stated on purpose: cookies go only to us
      headers: { Accept: 'application/json', ...CSRF_HEADERS, ...init?.headers },
    })
  } catch {
    // fetch rejects only on network failure (API down, offline).
    throw new ApiError(0, 'network_error', 'Could not reach the API')
  }
}

// --- refresh-on-401, single-flight ---------------------------------------------------------
//
// The access cookie lives 15 minutes. When it runs out every request gets a 401; we then
// call POST /auth/refresh ONCE and replay the requests. "Single-flight": all requests that
// fail at the same time share one refresh promise. Without it, five parallel 401s would send
// five refreshes with the same refresh token, and the server would treat the second one as a
// stolen-token reuse and log the user out.
//
// Across browser tabs the same problem exists, so the refresh also takes a Web Lock (when the
// browser supports it): a second tab waits, then refreshes with the cookie the first tab got.

let refreshInFlight: Promise<boolean> | null = null

async function postRefresh(): Promise<boolean> {
  try {
    const response = await send('/auth/refresh', { method: 'POST' })
    return response.ok
  } catch {
    return false
  }
}

/** Ask the API for a new access token. Resolves to false when the session is over. */
export function refreshSession(): Promise<boolean> {
  refreshInFlight ??= (
    typeof navigator !== 'undefined' && 'locks' in navigator
      ? navigator.locks.request('codelens-auth-refresh', postRefresh)
      : postRefresh()
  ).finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response = await send(path, init)

  if (response.status === 401 && !NO_REFRESH_PATHS.has(path) && (await refreshSession())) {
    response = await send(path, init) // replay once; a second 401 is final
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null
    throw new ApiError(
      response.status,
      body?.error.code ?? `http_${response.status}`,
      body?.error.message ?? response.statusText,
    )
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** Full-page navigation target that starts the GitHub login (not a fetch). */
export const GITHUB_LOGIN_URL = `${API_BASE}/auth/github/login`

export const api = {
  health: () => request<HealthResponse>('/health'),
  me: () => request<MeResponse>('/me'),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
}
