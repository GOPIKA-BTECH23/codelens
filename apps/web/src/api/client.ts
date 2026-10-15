import type { ApiErrorBody, HealthResponse } from './types'

// All API calls go to the same origin under /api. Vite proxies it in dev; in production a
// rewrite does the same (see CLAUDE.md decisions log, item 1).
const API_BASE = '/api'

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...init?.headers },
    })
  } catch {
    // fetch rejects only on network failure (API down, offline).
    throw new ApiError(0, 'network_error', 'Could not reach the API')
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null
    throw new ApiError(
      response.status,
      body?.error.code ?? `http_${response.status}`,
      body?.error.message ?? response.statusText,
    )
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<HealthResponse>('/health'),
}
