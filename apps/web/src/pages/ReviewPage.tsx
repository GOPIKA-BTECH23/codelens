import {
  Clock,
  Coins,
  Cpu,
  ExternalLink,
  GitBranch,
  Hash,
  ListFilter,
  Loader2,
  ShieldCheck,
  Tag,
  Timer,
  TriangleAlert,
} from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { useParams } from 'react-router'
import { usePull, useRepo, useReview, useUpdateFinding } from '../api/queries'
import type { FindingOut, FindingUpdate, ReviewOut, Severity } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { DiffView } from '../components/review/DiffView'
import { FindingCard } from '../components/review/FindingCard'
import { EmptyState, ErrorState } from '../components/States'
import {
  PrStateBadge,
  RunStatusBadge,
  SeverityIcon,
  SeverityBadge,
} from '../components/StatusBadges'
import { useToast } from '../components/toast'
import { Card } from '../components/ui/Card'
import { Checkbox } from '../components/ui/Field'
import { PageSkeleton } from '../components/ui/Skeleton'
import { buttonStyles } from '../components/ui/styles'
import { partitionByAnchor } from '../lib/diffAnchor'
import { errorMessage } from '../lib/errors'
import { formatCost, formatDateTime, formatLatency } from '../lib/format'
import { SEVERITIES } from '../lib/labels'

/** Model, prompt, latency, cost, tokens, commit, time: one compact line under the summary. */
function RunMetaBar({ run }: { run: ReviewOut['run'] }) {
  const items: { icon: ReactNode; label: string; value: string; mono?: boolean }[] = [
    { icon: <Cpu />, label: 'Model', value: run.model ?? '–', mono: true },
    { icon: <Tag />, label: 'Prompt version', value: run.prompt_version ?? '–', mono: true },
    { icon: <Timer />, label: 'Latency', value: formatLatency(run.latency_ms) },
    // Estimated at list price from a dated table; free-tier usage costs nothing real.
    { icon: <Coins />, label: 'Estimated cost', value: formatCost(run.cost_usd) },
    {
      icon: <Hash />,
      label: 'Tokens in / out',
      value: `${run.tokens_in ?? '–'} / ${run.tokens_out ?? '–'}`,
    },
    {
      icon: <GitBranch />,
      label: 'Commit',
      value: run.head_sha ? run.head_sha.slice(0, 7) : '–',
      mono: true,
    },
    { icon: <Clock />, label: 'Reviewed', value: formatDateTime(run.created_at) },
  ]
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border-t border-line bg-surface-2/50 px-5 py-3 sm:grid-cols-4 lg:flex lg:flex-wrap lg:gap-x-8">
      {items.map((item) => (
        <div key={item.label} className="min-w-0">
          <dt className="flex items-center gap-1 text-2xs font-medium tracking-wide text-muted uppercase [&_svg]:size-3">
            <span aria-hidden="true" className="contents">
              {item.icon}
            </span>
            {item.label}
          </dt>
          <dd className={`mt-0.5 truncate text-sm text-fg ${item.mono ? 'font-mono text-xs' : ''}`}>
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function SummaryCard({ data, open }: { data: ReviewOut; open: FindingOut[] }) {
  const counts = SEVERITIES.map((s) => [s, open.filter((f) => f.severity === s).length] as const)
  const inProgress = data.run.status === 'queued' || data.run.status === 'running'
  return (
    <section aria-labelledby="summary-heading">
      <Card className="overflow-hidden">
        <div className="space-y-4 p-5">
          <div className="flex flex-wrap items-center gap-3">
            <ShieldCheck aria-hidden="true" className="size-5 text-accent" />
            <h2 id="summary-heading" className="text-base font-semibold text-fg">
              Review summary
            </h2>
            <RunStatusBadge status={data.run.status} />
          </div>

          {inProgress && (
            <p role="status" className="flex items-center gap-2 text-sm text-muted">
              <Loader2 aria-hidden="true" className="size-4 motion-safe:animate-spin" />
              The review is in progress. This page updates by itself.
            </p>
          )}
          {data.run.error && (
            <p className="flex items-start gap-2 rounded-md border border-warning-line bg-warning-bg px-3 py-2 text-sm text-warning-fg">
              <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {data.run.error}
            </p>
          )}
          {data.summary?.what_changed && (
            <p className="max-w-3xl text-sm leading-6 text-fg">
              <span className="font-medium">What changed: </span>
              {data.summary.what_changed}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-fg">
              {open.length === 0
                ? 'No open findings'
                : `${open.length} open ${open.length === 1 ? 'finding' : 'findings'}`}
            </span>
            {counts
              .filter(([, n]) => n > 0)
              .map(([severity, n]) => (
                <span key={severity} className="inline-flex items-center gap-1 text-xs text-muted">
                  <SeverityBadge severity={severity} />
                  <span className="tabular-nums">×{n}</span>
                </span>
              ))}
          </div>

          {data.summary && data.summary.notes.length > 0 && (
            <details className="group text-sm">
              <summary className="cursor-pointer text-muted select-none hover:text-fg">
                Review notes ({data.summary.notes.length})
              </summary>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">
                {data.summary.notes.map((note, i) => (
                  <li key={i}>{note}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
        <RunMetaBar run={data.run} />
      </Card>
    </section>
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
    <div role="group" aria-label="Filter by severity" className="flex flex-wrap gap-1.5">
      {SEVERITIES.map((severity) => {
        const count = findings.filter((f) => f.severity === severity).length
        return (
          <button
            key={severity}
            type="button"
            aria-pressed={selected.has(severity)}
            disabled={count === 0 && !selected.has(severity)}
            onClick={() => onToggle(severity)}
            className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line bg-surface px-3 text-xs text-muted capitalize transition-colors duration-150 hover:border-line-strong hover:text-fg disabled:opacity-40 aria-pressed:border-accent-solid aria-pressed:bg-accent-soft aria-pressed:text-accent [&_svg]:size-3.5"
          >
            <span aria-hidden="true" className="contents">
              <SeverityIcon severity={severity} />
            </span>
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

  if (pull.isPending || review.isPending) return <PageSkeleton label="Loading review…" />
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
  const findings = data?.findings ?? []
  const open = findings.filter((f) => !f.dismissed)
  const dismissedCount = findings.length - open.length
  const visible = findings.filter(
    (f) => (showDismissed || !f.dismissed) && (severities.size === 0 || severities.has(f.severity)),
  )
  const { anchored, unanchored } = partitionByAnchor(data?.files ?? [], visible)
  const repoName = repo.data?.full_name

  return (
    <div>
      <PageHeader
        crumbs={[
          { label: 'Dashboard', to: '/dashboard' },
          ...(repo.data ? [{ label: repo.data.full_name, to: `/repos/${repo.data.id}` }] : []),
          { label: `#${pull.data.number}` },
        ]}
        title={
          <>
            <span className="font-normal text-muted">#{pull.data.number}</span> {pull.data.title}
          </>
        }
        badges={<PrStateBadge state={pull.data.state} />}
        meta={`by ${pull.data.author_login}`}
        actions={
          repoName && (
            <a
              href={`https://github.com/${repoName}/pull/${pull.data.number}`}
              className={buttonStyles()}
            >
              View on GitHub
              <ExternalLink aria-hidden="true" />
            </a>
          )
        }
      />

      {!data ? (
        <EmptyState
          title="Not reviewed yet"
          icon={<ShieldCheck />}
          action={
            repoName && (
              <a
                href={`https://github.com/${repoName}/pull/${pull.data.number}`}
                className={buttonStyles()}
              >
                Open the pull request on GitHub
                <ExternalLink aria-hidden="true" />
              </a>
            )
          }
        >
          CodeLens reviews a pull request when it is opened or updated. The result appears here.
        </EmptyState>
      ) : (
        <div className="space-y-8">
          <SummaryCard data={data} open={open} />

          <section aria-labelledby="findings-heading">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 id="findings-heading" className="text-base font-semibold text-fg">
                Findings ({open.length})
              </h2>
              {dismissedCount > 0 && (
                <label className="flex items-center gap-2 text-sm text-muted">
                  <Checkbox
                    checked={showDismissed}
                    onChange={(e) => setShowDismissed(e.target.checked)}
                  />
                  Show dismissed ({dismissedCount})
                </label>
              )}
            </div>
            {findings.length > 0 && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <ListFilter aria-hidden="true" className="size-4 text-muted" />
                <SeverityFilter
                  findings={findings.filter((f) => showDismissed || !f.dismissed)}
                  selected={severities}
                  onToggle={toggleSeverity}
                />
              </div>
            )}

            {findings.length === 0 ? (
              <div className="mt-4">
                <EmptyState title="No findings" icon={<ShieldCheck />}>
                  {data.run.status === 'succeeded'
                    ? 'The review found no issues in the reviewed changes.'
                    : 'There are no findings for this run.'}
                </EmptyState>
              </div>
            ) : visible.length === 0 ? (
              <p className="mt-4 text-sm text-muted">No findings match the current filter.</p>
            ) : (
              <p className="mt-3 text-xs text-muted">
                Findings are shown in the diff below, under the line they point at.
              </p>
            )}

            {unanchored.length > 0 && (
              <div className="mt-4 space-y-3">
                <h3 className="text-sm font-medium text-fg">Findings without a diff location</h3>
                {unanchored.map((finding) => (
                  <div key={finding.id}>
                    <p className="mb-1 font-mono text-xs text-muted">
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

          <section aria-labelledby="diff-heading">
            <h2 id="diff-heading" className="text-base font-semibold text-fg">
              Changes
            </h2>
            {data.files.length === 0 ? (
              <p className="mt-2 text-sm text-muted">
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
        </div>
      )}
    </div>
  )
}
