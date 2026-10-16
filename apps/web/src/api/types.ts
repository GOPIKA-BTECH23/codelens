// API types: ONE source of truth. The Pydantic models in apps/api are dumped to
// src/api/openapi.json (`uv run python -m scripts.dump_openapi`) and turned into schema.ts by
// `npm run gen:api-types`. CI fails when either file is stale. Nothing here is hand-copied from
// the backend: these are short names for generated types, plus the error shape the API's
// global handler returns (it is not a response model, so it is not in the schema).
import type { components } from './schema'

type Schemas = components['schemas']

export type HealthResponse = Schemas['HealthResponse']
export type MeResponse = Schemas['MeResponse']
export type InstallationSummary = Schemas['InstallationOut']
export type MemberRole = Schemas['MemberRole']

export type RepoOut = Schemas['RepoOut']
export type RepoDetail = Schemas['RepoDetail']
export type RepoSettings = Schemas['RepoSettings']
export type RepoSettingsUpdate = Schemas['RepoSettingsUpdate']
export type IndexRequested = Schemas['IndexRequested']
export type IndexStatus = Schemas['IndexStatus']
export type RunBrief = Schemas['RunBrief']
export type RunStatus = Schemas['RunStatus']

export type PullOut = Schemas['PullOut']
export type PullRequestState = Schemas['PullRequestState']

export type ReviewOut = Schemas['ReviewOut']
export type RunOut = Schemas['RunOut']
export type FindingOut = Schemas['FindingOut']
export type FindingUpdate = Schemas['FindingUpdate']
export type FindingFeedback = Schemas['FindingFeedback']
export type Severity = Schemas['Severity']
export type ReviewFileOut = Schemas['ReviewFileOut']
export type HunkOut = Schemas['HunkOut']
export type DiffLineOut = Schemas['DiffLineOut']

export type UsageOut = Schemas['UsageOut']
export type UsageDay = Schemas['UsageDay']

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

/** Every error response from the API has this shape. */
export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: { loc: (string | number)[]; msg: string }[]
  }
}
