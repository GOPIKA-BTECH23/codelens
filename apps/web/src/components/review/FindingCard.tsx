import type { FindingOut, FindingUpdate } from '../../api/types'
import { SEVERITY_TONE } from '../../lib/labels'
import { Badge } from '../Badge'
import { SuggestionView } from './SuggestionView'

interface Props {
  finding: FindingOut
  onUpdate: (change: FindingUpdate) => void
}

const button =
  'rounded border px-2.5 py-1 text-sm hover:bg-slate-100 dark:hover:bg-slate-800 aria-pressed:border-blue-600 aria-pressed:bg-blue-50 aria-pressed:text-blue-900 dark:aria-pressed:bg-blue-950 dark:aria-pressed:text-blue-100 border-slate-300 dark:border-slate-700'

/**
 * One finding with its feedback buttons. The buttons are toggles (aria-pressed): pressing the
 * active one again clears the vote. Message and suggestion are rendered as text, never HTML.
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
      className={`rounded border border-slate-300 bg-white p-3 text-left font-sans dark:border-slate-700 dark:bg-slate-900 ${
        finding.dismissed ? 'opacity-60' : ''
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={SEVERITY_TONE[finding.severity]}>{finding.severity}</Badge>
        <span className="font-medium">{finding.category}</span>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          confidence {finding.confidence.toFixed(2)}
        </span>
        {finding.dismissed && <Badge tone="neutral">Dismissed</Badge>}
      </div>

      <p className="mt-2 text-sm whitespace-pre-wrap">
        {finding.message ?? 'The text of this finding was removed (retention policy).'}
      </p>
      {finding.suggestion && <SuggestionView suggestion={finding.suggestion} />}

      <div role="group" aria-label="Rate this finding" className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          aria-pressed={helpful}
          className={button}
          onClick={() => onUpdate({ user_feedback: helpful ? null : 'helpful' })}
        >
          Useful
        </button>
        <button
          type="button"
          aria-pressed={notHelpful}
          className={button}
          onClick={() => onUpdate({ user_feedback: notHelpful ? null : 'not_helpful' })}
        >
          Not useful
        </button>
        <button
          type="button"
          className={`${button} ml-auto`}
          onClick={() => onUpdate({ dismissed: !finding.dismissed })}
        >
          {finding.dismissed ? 'Restore' : 'Dismiss'}
        </button>
      </div>
    </article>
  )
}
