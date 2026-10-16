import type { IndexStatus, PullRequestState, RunStatus, Severity } from '../api/types'

export type Tone = 'neutral' | 'good' | 'warn' | 'bad' | 'info'

export const RUN_STATUS: Record<RunStatus, { label: string; tone: Tone }> = {
  queued: { label: 'Queued', tone: 'neutral' },
  running: { label: 'Reviewing…', tone: 'info' },
  succeeded: { label: 'Reviewed', tone: 'good' },
  failed: { label: 'Failed', tone: 'bad' },
  superseded: { label: 'Superseded', tone: 'neutral' },
  skipped: { label: 'Skipped', tone: 'warn' },
}

export const PR_STATE: Record<PullRequestState, { label: string; tone: Tone }> = {
  open: { label: 'Open', tone: 'good' },
  merged: { label: 'Merged', tone: 'info' },
  closed: { label: 'Closed', tone: 'neutral' },
}

// Indexing arrives in milestone M8. Until then every status is shown as "not available yet"
// instead of pretending the feature works (a repo marked `failed` by the stub is not an error
// the user can fix).
export const INDEXING_AVAILABLE = false

export function indexStatusLabel(status: IndexStatus): { label: string; tone: Tone } {
  if (!INDEXING_AVAILABLE) return { label: 'Indexing: not available yet', tone: 'neutral' }
  const map: Record<IndexStatus, { label: string; tone: Tone }> = {
    not_indexed: { label: 'Not indexed', tone: 'neutral' },
    queued: { label: 'Index queued', tone: 'info' },
    indexing: { label: 'Indexing…', tone: 'info' },
    ready: { label: 'Indexed', tone: 'good' },
    failed: { label: 'Index failed', tone: 'bad' },
  }
  return map[status]
}

/** Strongest first: the order of the filter buttons and of "worst finding" comparisons. */
export const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low', 'info']

export const SEVERITY_TONE: Record<Severity, Tone> = {
  critical: 'bad',
  high: 'bad',
  medium: 'warn',
  low: 'info',
  info: 'neutral',
}
