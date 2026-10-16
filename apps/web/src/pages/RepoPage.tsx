import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { PAGE_SIZE, usePulls, useRepo } from '../api/queries'
import type { PullOut, PullRequestState } from '../api/types'
import { Badge } from '../components/Badge'
import { Pagination } from '../components/Pagination'
import { EmptyState, ErrorState, Loading } from '../components/States'
import { timeAgo } from '../lib/format'
import { PR_STATE, RUN_STATUS, indexStatusLabel } from '../lib/labels'

function PullRow({ pull }: { pull: PullOut }) {
  const run = pull.latest_run
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <Link
          to={`/pulls/${pull.id}`}
          className="font-medium text-blue-700 hover:underline dark:text-blue-300"
        >
          #{pull.number} {pull.title}
        </Link>
        <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">
          {pull.author_login} · updated {timeAgo(pull.updated_at)}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Badge tone={PR_STATE[pull.state].tone}>{PR_STATE[pull.state].label}</Badge>
        {run ? (
          <>
            <Badge tone={RUN_STATUS[run.status].tone}>{RUN_STATUS[run.status].label}</Badge>
            {run.status === 'succeeded' && (
              <span className="text-sm text-slate-600 dark:text-slate-400">
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

  if (repo.isPending) return <Loading label="Loading repository…" />
  if (repo.isError) return <ErrorState error={repo.error} onRetry={() => void repo.refetch()} />
  const index = indexStatusLabel(repo.data.index_status)

  return (
    <div>
      <p className="text-sm">
        <Link to="/dashboard" className="text-blue-700 hover:underline dark:text-blue-300">
          ← Dashboard
        </Link>
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">{repo.data.full_name}</h1>
        <Badge tone={index.tone}>{index.label}</Badge>
      </div>
      <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
        Default branch <code>{repo.data.default_branch}</code> · your role: {repo.data.role}
      </p>

      <section aria-labelledby="pulls-heading" className="mt-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="pulls-heading" className="font-semibold">
            Pull requests
          </h2>
          <label className="text-sm">
            <span className="mr-2">State</span>
            <select
              value={state ?? ''}
              onChange={(e) => {
                setState((e.target.value || undefined) as PullRequestState | undefined)
                setPage(0)
              }}
              className="rounded border border-slate-300 bg-transparent px-2 py-1.5 dark:border-slate-700 dark:bg-slate-950"
            >
              <option value="">All</option>
              <option value="open">Open</option>
              <option value="merged">Merged</option>
              <option value="closed">Closed</option>
            </select>
          </label>
        </div>

        <div className="mt-2">
          {pulls.isPending ? (
            <Loading label="Loading pull requests…" />
          ) : pulls.isError ? (
            <ErrorState error={pulls.error} onRetry={() => void pulls.refetch()} />
          ) : pulls.data.items.length === 0 ? (
            <EmptyState title="No pull requests">
              {state
                ? 'None in this state.'
                : 'Open a pull request on GitHub and CodeLens will review it.'}
            </EmptyState>
          ) : (
            <>
              <ul className="divide-y divide-slate-200 rounded border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
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
      </section>
    </div>
  )
}
