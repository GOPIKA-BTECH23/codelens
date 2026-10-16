import { Eye, EyeOff, ThumbsDown, ThumbsUp } from 'lucide-react'
import type { ReactNode } from 'react'
import type { FindingOut, FindingUpdate, Severity } from '../../api/types'
import { SeverityBadge } from '../StatusBadges'
import { Badge } from '../ui/Badge'
import { SuggestionView } from './SuggestionView'

interface Props {
  finding: FindingOut
  onUpdate: (change: FindingUpdate) => void
}

// Full class names (not built from parts) so Tailwind can see them.
const SEVERITY_EDGE: Record<Severity, string> = {
  critical: 'border-l-danger-fg',
  high: 'border-l-orange-fg',
  medium: 'border-l-warning-fg',
  low: 'border-l-info-fg',
  info: 'border-l-line-strong',
}

const toggle =
  'inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium ' +
  'transition-colors duration-150 [&_svg]:size-3.5 ' +
  'border-line bg-surface text-muted hover:border-line-strong hover:text-fg'

const PRESSED = {
  helpful:
    'aria-pressed:border-success-line aria-pressed:bg-success-bg aria-pressed:text-success-fg',
  notHelpful:
    'aria-pressed:border-danger-line aria-pressed:bg-danger-bg aria-pressed:text-danger-fg',
}

function Toggle({
  pressed,
  onClick,
  icon,
  className = '',
  children,
}: {
  pressed?: boolean
  onClick: () => void
  icon: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={`${toggle} ${className}`}
    >
      <span aria-hidden="true" className="contents">
        {icon}
      </span>
      {children}
    </button>
  )
}

/**
 * One finding with its feedback controls. Useful / Not useful are toggles (aria-pressed):
 * pressing the active one again clears the vote. Message and suggestion are rendered as text,
 * never HTML.
 */
export function FindingCard({ finding, onUpdate }: Props) {
  const lines =
    finding.end_line && finding.end_line !== finding.line
      ? `lines ${finding.line}–${finding.end_line}`
      : `line ${finding.line}`
  const helpful = finding.user_feedback === 'helpful'
  const notHelpful = finding.user_feedback === 'not_helpful'

  return (
    <article
      aria-label={`${finding.severity} finding: ${finding.category} in ${finding.file}, ${lines}`}
      className={`rounded-lg border border-l-4 border-line bg-surface p-4 text-left font-sans shadow-card transition-opacity duration-150 ${
        SEVERITY_EDGE[finding.severity]
      } ${finding.dismissed ? 'opacity-60' : ''}`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <SeverityBadge severity={finding.severity} />
        <span className="font-mono text-sm font-medium text-fg">{finding.category}</span>
        <span className="text-xs text-muted">
          {lines} · confidence {finding.confidence.toFixed(2)}
        </span>
        {finding.dismissed && (
          <Badge tone="neutral" icon={<EyeOff />}>
            Dismissed
          </Badge>
        )}
      </div>

      <p className="mt-2.5 text-sm leading-6 whitespace-pre-wrap text-fg">
        {finding.message ?? 'The text of this finding was removed (retention policy).'}
      </p>
      {finding.suggestion && <SuggestionView suggestion={finding.suggestion} />}

      <div
        role="group"
        aria-label="Rate this finding"
        className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-3"
      >
        <span className="mr-1 text-xs text-muted">Was this useful?</span>
        <Toggle
          pressed={helpful}
          className={PRESSED.helpful}
          icon={<ThumbsUp />}
          onClick={() => onUpdate({ user_feedback: helpful ? null : 'helpful' })}
        >
          Useful
        </Toggle>
        <Toggle
          pressed={notHelpful}
          className={PRESSED.notHelpful}
          icon={<ThumbsDown />}
          onClick={() => onUpdate({ user_feedback: notHelpful ? null : 'not_helpful' })}
        >
          Not useful
        </Toggle>
        <Toggle
          className="ml-auto"
          icon={finding.dismissed ? <Eye /> : <EyeOff />}
          onClick={() => onUpdate({ dismissed: !finding.dismissed })}
        >
          {finding.dismissed ? 'Restore' : 'Dismiss'}
        </Toggle>
      </div>
    </article>
  )
}
