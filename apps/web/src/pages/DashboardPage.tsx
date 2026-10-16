import { useDeferredValue, useState } from 'react'
import { Link } from 'react-router'
import { useRepos, PAGE_SIZE } from '../api/queries'
import type { MemberRole, RepoOut } from '../api/types'
import { useAuth } from '../auth/context'
import { Badge } from '../components/Badge'
import { Pagination } from '../components/Pagination'
import { EmptyState, ErrorState, Loading } from '../components/States'
import { timeAgo } from '../lib/format'
import { RUN_STATUS, indexStatusLabel } from '../lib/labels'

const ROLE_LABEL: Record<MemberRole, string> = { owner: 'Owner', admin: 'Admin', member: 'Member' }

function RepoRow({ repo }: { repo: RepoOut }) {
  const index = indexStatusLabel(repo.index_status)
  const run = repo.latest_run
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <Link
          to={`/repos/${repo.id}`}
          className="font-medium text-blue-700 hover:underline dark:text-blue-300"
        >
          {repo.full_name}
        </Link>
        {repo.is_private && (
          <span className="ml-2 text-xs text-slate-500 dark:text-slate-400">private</span>
        )}
        <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-400">
          {run ? (
            <>
              Latest review:{' '}
              <Link to={`/pulls/${run.pull_request_id}`} className="hover:underline">
                #{run.pull_number} {run.pull_title}
              </Link>{' '}
              · {timeAgo(run.created_at)}
            </>
          ) : (
            'No reviews yet'
          )}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {run && <Badge tone={RUN_STATUS[run.status].tone}>{RUN_STATUS[run.status].label}</Badge>}
        <Badge tone={index.tone}>{index.label}</Badge>
      </div>
    </li>
  )
}

export function DashboardPage() {
  const { me } = useAuth()
  const [page, setPage] = useState(0)
  const [search, setSearch] = useState('')
  const q = useDeferredValue(search.trim())
  const repos = useRepos(page, q)
  if (!me) return null // ProtectedRoute only renders this when signed in

  return (
    <div>
      <h1 className="text-2xl font-bold">Welcome, {me.user.name ?? me.user.login}</h1>

      <section aria-labelledby="installations-heading" className="mt-8">
        <h2 id="installations-heading" className="font-semibold">
          Your installations
        </h2>
        {me.installations.length === 0 ? (
          <div className="mt-2">
            <EmptyState title="No installations yet">
              Install the CodeLens GitHub App on an account or organisation, then sign in again.
            </EmptyState>
          </div>
        ) : (
          <ul className="mt-2 flex flex-wrap gap-2">
            {me.installations.map((inst) => (
              <li
                key={inst.id}
                className="flex items-center gap-2 rounded border border-slate-200 px-3 py-1.5 dark:border-slate-800"
              >
                <span>{inst.account_login}</span>
                <span className="text-sm text-slate-500 dark:text-slate-400">
                  ({inst.account_type})
                </span>
                <Badge tone="neutral">{ROLE_LABEL[inst.role]}</Badge>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="repos-heading" className="mt-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="repos-heading" className="font-semibold">
            Repositories
          </h2>
          <label className="text-sm">
            <span className="sr-only">Search repositories</span>
            <input
              type="search"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value)
                setPage(0)
              }}
              placeholder="Search repositories"
              className="rounded border border-slate-300 bg-transparent px-3 py-1.5 dark:border-slate-700"
            />
          </label>
        </div>

        <div className="mt-2">
          {repos.isPending ? (
            <Loading label="Loading repositories…" />
          ) : repos.isError ? (
            <ErrorState error={repos.error} onRetry={() => void repos.refetch()} />
          ) : repos.data.items.length === 0 ? (
            <EmptyState title={q ? 'No repositories match your search' : 'No repositories yet'}>
              {q
                ? 'Try a different name.'
                : 'Repositories appear here once the app is installed on them and you have access.'}
            </EmptyState>
          ) : (
            <>
              <ul className="divide-y divide-slate-200 rounded border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
                {repos.data.items.map((repo) => (
                  <RepoRow key={repo.id} repo={repo} />
                ))}
              </ul>
              <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                total={repos.data.total}
                onPage={setPage}
              />
            </>
          )}
        </div>
      </section>
    </div>
  )
}
