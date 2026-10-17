import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Circle,
  CircleAlert,
  CircleSlash,
  Clock,
  GitMerge,
  GitPullRequest,
  GitPullRequestClosed,
  Info,
  Loader2,
  SkipForward,
  XCircle,
} from 'lucide-react'
import type { ReactNode } from 'react'
import type { PullRequestState, RunStatus, Severity } from '../api/types'
import { PR_STATE, SEVERITY_LABEL, SEVERITY_TONE, runBadge } from '../lib/labels'
import { Badge } from './ui/Badge'

const RUN_ICON: Record<RunStatus, ReactNode> = {
  queued: <Clock />,
  running: <Loader2 className="motion-safe:animate-spin" />,
  succeeded: <CheckCircle2 />,
  failed: <XCircle />,
  superseded: <CircleSlash />,
  skipped: <SkipForward />,
}

export function RunStatusBadge({
  status,
  reviewPosted,
}: {
  status: RunStatus
  reviewPosted: boolean
}) {
  const { label, tone } = runBadge(status, reviewPosted)
  const notReviewed = status === 'succeeded' && !reviewPosted
  return (
    <Badge tone={tone} icon={notReviewed ? <CircleSlash /> : RUN_ICON[status]}>
      {label}
    </Badge>
  )
}

const PR_ICON: Record<PullRequestState, ReactNode> = {
  open: <GitPullRequest />,
  merged: <GitMerge />,
  closed: <GitPullRequestClosed />,
}

export function PrStateBadge({ state }: { state: PullRequestState }) {
  return (
    <Badge tone={PR_STATE[state].tone} icon={PR_ICON[state]}>
      {PR_STATE[state].label}
    </Badge>
  )
}

/** Each severity has its own icon shape as well as its own colour and word. */
const SEVERITY_ICON: Record<Severity, ReactNode> = {
  critical: <AlertOctagon />,
  high: <AlertTriangle />,
  medium: <CircleAlert />,
  low: <Info />,
  info: <Circle />,
}

export function SeverityIcon({ severity }: { severity: Severity }) {
  return SEVERITY_ICON[severity]
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <Badge tone={SEVERITY_TONE[severity]} icon={SEVERITY_ICON[severity]}>
      {SEVERITY_LABEL[severity]}
    </Badge>
  )
}
