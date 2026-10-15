// API types live here and nowhere else. They are hand-written for now and mirror the
// Pydantic models in apps/api; M6 revisits generating them from the OpenAPI schema.

/** GET /health */
export interface HealthResponse {
  status: 'ok'
  version: string
  database: 'ok' | 'unavailable'
  /** Current Alembic revision; null when the database is unreachable or not migrated. */
  revision: string | null
}

/** Roles inside an installation, strongest first: owner > admin > member. */
export type MemberRole = 'owner' | 'admin' | 'member'

export interface InstallationSummary {
  id: string
  github_installation_id: number
  account_login: string
  account_type: 'user' | 'organization'
  role: MemberRole
}

/** GET /me */
export interface MeResponse {
  user: {
    id: string
    login: string
    name: string | null
    avatar_url: string | null
  }
  installations: InstallationSummary[]
}

/** Every error response from the API has this shape. */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: { loc: (string | number)[]; msg: string }[]
  }
}
