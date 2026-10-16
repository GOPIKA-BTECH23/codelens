import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { useRepo, usePull, useReview, useUpdateFinding } from '../api/queries'
import type { FindingOut, FindingUpdate, ReviewOut, Severity } from '../api/types'
import { Badge } from '../components/Badge'
import { DiffView } from '../components/review/DiffView'
import { FindingCard } from '../components/review/FindingCard'
import { EmptyState, ErrorState, Loading } from '../components/States'
import { useToast } from '../components/toast'
import { partitionByAnchor } from '../lib/diffAnchor'
import { errorMessage } from '../lib/errors'
import { formatCost, formatDateTime, formatLatency } from '../lib/format'
import { PR_STATE, RUN_STATUS, SEVERITIES } from '../lib/labels'

function RunMetadata({ run }: { run: ReviewOut['run'] }) {
  const items: [string, string][] = [
    ['Model', run.model ?? '–'],
    ['Prompt version', run.prompt_version ?? '–'],
    ['Latency', formatLatency(run.latency_ms)],
    // Estimated at list price from a dated table; free-tier usage costs nothing real.
    ['Estimated cost', formatCost(run.cost_usd)],
    ['Tokens in / out', `${run.tokens_in ?? '–'} / ${run.tokens_out ?? '–'}`],
    ['Commit', run.head_sha ? run.head_sha.slice(0, 7) : '–'],
    ['Reviewed', formatDateTime(run.created_at)],
  ]
  return (
    <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt className="text-slate-500 dark:text-slate-400">{label}</dt>
          <dd className="font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  )
}

function SeverityFilter({
  findings,
  selected,
  onToggle,
}: {
  findings: FindingOut[]
  selected: Set<Severity>
  onToggle: (severity: Severity) => void
}) {
  return (
    <div role="group" aria-label="Filter by severity" className="flex flex-wrap gap-2">
      {SEVERITIES.map((severity) => {
        const count = findings.filter((f) => f.severity === severity).length
        return (
          <button
            key={severity}
            type="button"
            aria-pressed={selected.has(severity)}
            disabled={count === 0 && !selected.has(severity)}
            onClick={() => onToggle(severity)}
            className="rounded-full border border-slate-300 px-3 py-1 text-sm capitalize hover:bg-slate-100 disabled:opacity-40 aria-pressed:border-blue-600 aria-pressed:bg-blue-50 aria-pressed:text-blue-900 dark:border-slate-700 dark:hover:bg-slate-800 dark:aria-pressed:bg-blue-950 dark:aria-pressed:text-blue-100"
          >
            {severity} ({count})
          </button>
        )
      })}
    </div>
  )
}

export function ReviewPage() {
  const { pullId = '' } = useParams()
  const toast = useToast()
  const pull = usePull(pullId)
  const repo = useRepo(pull.data?.repository_id ?? '')
  const review = useReview(pullId)
  const update = useUpdateFinding(pullId, (error) =>
    toast.show(`Could not save your change: ${errorMessage(error)}`, 'error'),
  )
  const [severities, setSeverities] = useState<Set<Severity>>(new Set())
  const [showDismissed, setShowDismissed] = useState(false)

  if (pull.isPending || review.isPending) return <Loading label="Loading review…" />
  if (pull.isError) return <ErrorState error={pull.error} onRetry={() => void pull.refetch()} />
  if (review.isError)
    return <ErrorState error={review.error} onRetry={() => void review.refetch()} />

  const send = (findingId: string, change: FindingUpdate) =>
    update.mutate({ id: findingId, change })
  const toggleSeverity = (severity: Severity) =>
    setSeverities((current) => {
      const next = new Set(current)
      if (next.has(severity)) next.delete(severity)
      else next.add(severity)
      return next
    })

  const data = review.data
  const dismissedCount = data?.findings.filter((f) => f.dismissed).length ?? 0
  const visible = (data?.findings ?? []).filter(
    (f) => (showDismissed || !f.dismissed) && (severities.size === 0 || severities.has(f.severity)),
  )
  const { anchored, unanchored } = partitionByAnchor(data?.files ?? [], visible)

  return (
    <div>
      <p className="text-sm">
        <Link
          to={repo.data ? `/repos/${repo.data.id}` : '/dashboard'}
          className="text-blue-700 hover:underline dark:text-blue-300"
        >
          ← {repo.data?.full_name ?? 'Dashboard'}
        </Link>
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">
          #{pull.data.number} {pull.data.title}
        </h1>
        <Badge tone={PR_STATE[pull.data.state].tone}>{PR_STATE[pull.data.state].label}</Badge>
      </div>
      <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">by {pull.data.author_login}</p>

      {!data ? (
        <div className="mt-6">
          <EmptyState title="Not reviewed yet">
            CodeLens reviews a pull request when it is opened or updated. The result appears here.
          </EmptyState>
        </div>
      ) : (
        <>
          <section
            aria-labelledby="summary-heading"
            className="mt-6 rounded border border-slate-200 p-4 dark:border-slate-800"
          >
            <div className="flex flex-wrap items-center gap-3">
              <h2 id="summary-heading" className="font-semibold">
                Review summary
              </h2>
              <Badge tone={RUN_STATUS[data.run.status].tone}>
                {RUN_STATUS[data.run.status].label}
              </Badge>
            </div>
            {(data.run.status === 'queued' || data.run.status === 'running') && (
              <p role="status" className="mt-2 text-sm text-slate-600 dark:text-slate-400">
                The review is in progress. This page updates by itself.
              </p>
            )}
            {data.run.error && (
              <p className="mt-2 text-sm text-amber-800 dark:text-amber-300">{data.run.error}</p>
            )}
            {data.summary?.what_changed && (
              <p className="mt-3">
                <span className="font-medium">What changed: </span>
                {data.summary.what_changed}
              </p>
            )}
            {data.summary && data.summary.notes.length > 0 && (
              <details className="mt-2 text-sm">
                <summary className="cursor-pointer">Review notes</summary>
                <ul className="mt-1 list-disc pl-5">
                  {data.summary.notes.map((note, i) => (
                    <li key={i}>{note}</li>
                  ))}
                </ul>
              </details>
            )}
            <RunMetadata run={data.run} />
          </section>

          <section aria-labelledby="findings-heading" className="mt-8">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 id="findings-heading" className="font-semibold">
                Findings ({data.findings.length - dismissedCount})
              </h2>
              {dismissedCount > 0 && (
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={showDismissed}
                    onChange={(e) => setShowDismissed(e.target.checked)}
                  />
                  Show dismissed ({dismissedCount})
                </label>
              )}
            </div>
            <div className="mt-3">
              <SeverityFilter
                findings={data.findings.filter((f) => showDismissed || !f.dismissed)}
                selected={severities}
                onToggle={toggleSeverity}
              />
            </div>

            {data.findings.length === 0 ? (
              <div className="mt-4">
                <EmptyState title="No findings">
                  {data.run.status === 'succeeded'
                    ? 'The review found no issues in the reviewed changes.'
                    : 'There are no findings for this run.'}
                </EmptyState>
              </div>
            ) : visible.length === 0 ? (
              <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">
                No findings match the current filter.
              </p>
            ) : null}

            {unanchored.length > 0 && (
              <div className="mt-4 space-y-3">
                <h3 className="text-sm font-medium">Findings without a diff location</h3>
                {unanchored.map((finding) => (
                  <div key={finding.id}>
                    <p className="mb-1 font-mono text-xs text-slate-600 dark:text-slate-400">
                      {finding.file}:{finding.line}
                    </p>
                    <FindingCard
                      finding={finding}
                      onUpdate={(change) => send(finding.id, change)}
                    />
                  </div>
                ))}
              </div>
            )}
          </section>

          <section aria-labelledby="diff-heading" className="mt-8">
            <h2 id="diff-heading" className="font-semibold">
              Changes
            </h2>
            {data.files.length === 0 ? (
              <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
                The diff was not stored for this review, so only the findings are shown.
              </p>
            ) : (
              <div className="mt-3 space-y-4">
                {data.files.map((file) => (
                  <DiffView
                    key={file.path}
                    file={file}
                    findings={anchored.get(file.path) ?? []}
                    onUpdate={send}
                  />
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
