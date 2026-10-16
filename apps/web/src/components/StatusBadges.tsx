import {
  AlertOctagon,
  AlertTriangle,
  CheckCircle2,
  Circle,
  CircleAlert,
  CircleDashed,
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
import type { IndexStatus, PullRequestState, RunStatus, Severity } from '../api/types'
import {
  PR_STATE,
  RUN_STATUS,
  SEVERITY_LABEL,
  SEVERITY_TONE,
  indexStatusLabel,
} from '../lib/labels'
import { Badge } from './ui/Badge'

const RUN_ICON: Record<RunStatus, ReactNode> = {
  queued: <Clock />,
  running: <Loader2 className="motion-safe:animate-spin" />,
  succeeded: <CheckCircle2 />,
  failed: <XCircle />,
  superseded: <CircleSlash />,
  skipped: <SkipForward />,
}

export function RunStatusBadge({ status }: { status: RunStatus }) {
  return (
    <Badge tone={RUN_STATUS[status].tone} icon={RUN_ICON[status]}>
      {RUN_STATUS[status].label}
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

export function IndexBadge({ status }: { status: IndexStatus }) {
  const { label, tone } = indexStatusLabel(status)
  return (
    <Badge tone={tone} icon={<CircleDashed />}>
      {label}
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
