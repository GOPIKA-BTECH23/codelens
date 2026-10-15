// API types live here and nowhere else. They are hand-written for now and mirror the
// Pydantic models in apps/api; M6 revisits generating them from the OpenAPI schema.

/** GET /health */
export interface HealthResponse {
  status: 'ok'
  version: string
  database: 'ok' | 'error' | 'not_configured'
}

/** Every error response from the API has this shape. */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: { loc: (string | number)[]; msg: string }[]
  }
}
