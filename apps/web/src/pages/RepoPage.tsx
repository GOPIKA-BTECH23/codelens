import { ExternalLink, GitBranch, GitPullRequest, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { PAGE_SIZE, usePulls, useRepo } from '../api/queries'
import type { PullOut, PullRequestState } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { Pagination } from '../components/Pagination'
import { RepoSettingsForm } from '../components/RepoSettingsForm'
import { EmptyState, ErrorState } from '../components/States'
import { PrStateBadge, RunStatusBadge } from '../components/StatusBadges'
import { Badge } from '../components/ui/Badge'
import { Card, CardHeader } from '../components/ui/Card'
import { Select } from '../components/ui/Field'
import { ListSkeleton, PageSkeleton } from '../components/ui/Skeleton'
import { buttonStyles } from '../components/ui/styles'
import { timeAgo } from '../lib/format'

const STATE_ICON_TONE: Record<PullRequestState, string> = {
  open: 'text-success-fg',
  merged: 'text-accent',
  closed: 'text-muted',
}

function PullRow({ pull }: { pull: PullOut }) {
  const run = pull.latest_run
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3.5 transition-colors duration-150 hover:bg-surface-2/60">
      <div className="flex min-w-0 items-start gap-3">
        <GitPullRequest
          aria-hidden="true"
          className={`mt-0.5 size-4 shrink-0 ${STATE_ICON_TONE[pull.state]}`}
        />
        <div className="min-w-0">
          <Link
            to={`/pulls/${pull.id}`}
            className="font-medium text-fg hover:text-accent hover:underline"
          >
            <span className="text-muted">#{pull.number}</span> {pull.title}
          </Link>
          <p className="mt-0.5 text-sm text-muted">
            by {pull.author_login} · updated {timeAgo(pull.updated_at)}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 pl-7 sm:pl-0">
        <PrStateBadge state={pull.state} />
        {run ? (
          <>
            <RunStatusBadge status={run.status} reviewPosted={run.review_posted} />
            {run.status === 'succeeded' && run.review_posted && (
              <span className="ml-1 text-xs text-muted tabular-nums">
                {run.findings_count} {run.findings_count === 1 ? 'finding' : 'findings'}
              </span>
            )}
          </>
        ) : (
          <Badge tone="neutral">Not reviewed</Badge>
        )}
      </div>
    </li>
  )
}

export function RepoPage() {
  const { repoId = '' } = useParams()
  const [state, setState] = useState<PullRequestState | undefined>(undefined)
  const [page, setPage] = useState(0)
  const repo = useRepo(repoId)
  const pulls = usePulls(repoId, state, page)

  if (repo.isPending) return <PageSkeleton label="Loading repository…" />
  if (repo.isError) return <ErrorState error={repo.error} onRetry={() => void repo.refetch()} />
  const isAdmin = repo.data.role !== 'member'
  const githubUrl = `https://github.com/${repo.data.full_name}`

  return (
    <div>
      <PageHeader
        crumbs={[{ label: 'Dashboard', to: '/dashboard' }, { label: repo.data.full_name }]}
        title={repo.data.full_name}
        badges={repo.data.is_private && <Badge tone="neutral">Private</Badge>}
        meta={
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="inline-flex items-center gap-1">
              <GitBranch aria-hidden="true" className="size-3.5" />
              <code className="text-xs">{repo.data.default_branch}</code>
            </span>
            <span>your role: {repo.data.role}</span>
          </span>
        }
        actions={
          <a href={githubUrl} className={buttonStyles()}>
            View on GitHub
            <ExternalLink aria-hidden="true" />
          </a>
        }
      />

      <div className={isAdmin ? 'grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]' : ''}>
        <section aria-labelledby="pulls-heading">
          <Card>
            <CardHeader
              title="Pull requests"
              titleId="pulls-heading"
              description={pulls.data ? `${pulls.data.total} total` : undefined}
              actions={
                <label className="flex items-center gap-2 text-xs text-muted">
                  <span>State</span>
                  <Select
                    value={state ?? ''}
                    onChange={(e) => {
                      setState((e.target.value || undefined) as PullRequestState | undefined)
                      setPage(0)
                    }}
                  >
                    <option value="">All</option>
                    <option value="open">Open</option>
                    <option value="merged">Merged</option>
                    <option value="closed">Closed</option>
                  </Select>
                </label>
              }
            />
            <div className="p-3">
              {pulls.isPending ? (
                <ListSkeleton label="Loading pull requests…" />
              ) : pulls.isError ? (
                <ErrorState error={pulls.error} onRetry={() => void pulls.refetch()} />
              ) : pulls.data.items.length === 0 ? (
                <EmptyState
                  title="No pull requests"
                  icon={<GitPullRequest />}
                  action={
                    state ? undefined : (
                      <a
                        href={`${githubUrl}/compare`}
                        className={buttonStyles({ variant: 'primary' })}
                      >
                        <Plus aria-hidden="true" />
                        Open a pull request on GitHub
                      </a>
                    )
                  }
                >
                  {state
                    ? 'None in this state.'
                    : 'CodeLens reviews a pull request as soon as it is opened or updated.'}
                </EmptyState>
              ) : (
                <>
                  <ul className="divide-y divide-line overflow-hidden rounded-md border border-line">
                    {pulls.data.items.map((pull) => (
                      <PullRow key={pull.id} pull={pull} />
                    ))}
                  </ul>
                  <Pagination
                    page={page}
                    pageSize={PAGE_SIZE}
                    total={pulls.data.total}
                    onPage={setPage}
                  />
                </>
              )}
            </div>
          </Card>
        </section>

        {isAdmin && (
          <section aria-labelledby="settings-heading">
            <Card>
              <CardHeader
                title="Review settings"
                titleId="settings-heading"
                description="Applies to the next review of this repository."
              />
              <div className="p-4">
                <RepoSettingsForm
                  key={JSON.stringify(repo.data.settings)}
                  repoId={repoId}
                  settings={repo.data.settings}
                />
              </div>
            </Card>
          </section>
        )}
      </div>
    </div>
  )
}
