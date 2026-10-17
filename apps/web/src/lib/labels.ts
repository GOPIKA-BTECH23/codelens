import type { PullRequestState, RunStatus, Severity } from '../api/types'

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'orange' | 'danger' | 'info'

export const RUN_STATUS: Record<RunStatus, { label: string; tone: Tone }> = {
  queued: { label: 'Queued', tone: 'neutral' },
  running: { label: 'Reviewing…', tone: 'info' },
  succeeded: { label: 'Reviewed', tone: 'success' },
  failed: { label: 'Failed', tone: 'danger' },
  superseded: { label: 'Superseded', tone: 'neutral' },
  skipped: { label: 'Skipped', tone: 'warning' },
}

/**
 * What the badge for a run says. `succeeded` only means the worker finished; it is "Reviewed"
 * only when a review was really posted to GitHub (e.g. the M4 stub succeeds without posting).
 */
export function runBadge(
  status: RunStatus,
  reviewPosted: boolean,
): { label: string; tone: Tone; reviewed: boolean } {
  if (status === 'succeeded' && !reviewPosted) {
    return { label: 'Not reviewed', tone: 'neutral', reviewed: false }
  }
  return { ...RUN_STATUS[status], reviewed: status === 'succeeded' }
}

export const PR_STATE: Record<PullRequestState, { label: string; tone: Tone }> = {
  open: { label: 'Open', tone: 'success' },
  merged: { label: 'Merged', tone: 'accent' },
  closed: { label: 'Closed', tone: 'neutral' },
}

/** Strongest first: the order of the filter buttons and of "worst finding" comparisons. */
export const SEVERITIES: Severity[] = ['critical', 'high', 'medium', 'low', 'info']

// Five severities, five distinct tones. Each badge also carries its own icon and the word, so
// severity never depends on telling colours apart.
export const SEVERITY_TONE: Record<Severity, Tone> = {
  critical: 'danger',
  high: 'orange',
  medium: 'warning',
  low: 'info',
  info: 'neutral',
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  info: 'Info',
}
