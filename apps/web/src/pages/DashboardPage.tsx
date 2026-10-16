import { Building2, ExternalLink, FolderGit2, Lock, Search, User } from 'lucide-react'
import { useDeferredValue, useState } from 'react'
import { Link } from 'react-router'
import { PAGE_SIZE, useRepos } from '../api/queries'
import type { MemberRole, RepoOut } from '../api/types'
import { useAuth } from '../auth/context'
import { PageHeader } from '../components/PageHeader'
import { Pagination } from '../components/Pagination'
import { EmptyState, ErrorState } from '../components/States'
import { IndexBadge, RunStatusBadge } from '../components/StatusBadges'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card, CardHeader } from '../components/ui/Card'
import { Input } from '../components/ui/Field'
import { ListSkeleton } from '../components/ui/Skeleton'
import { buttonStyles } from '../components/ui/styles'
import { timeAgo } from '../lib/format'

const ROLE_LABEL: Record<MemberRole, string> = { owner: 'Owner', admin: 'Admin', member: 'Member' }
// Where a user manages which accounts and repositories the GitHub App can access.
const GITHUB_INSTALLATIONS_URL = 'https://github.com/settings/installations'

function RepoRow({ repo }: { repo: RepoOut }) {
  const run = repo.latest_run
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3.5 transition-colors duration-150 hover:bg-surface-2/60">
      <div className="flex min-w-0 items-start gap-3">
        <span
          aria-hidden="true"
          className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md border border-line bg-surface-2 text-muted [&_svg]:size-4"
        >
          {repo.is_private ? <Lock /> : <FolderGit2 />}
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/repos/${repo.id}`}
              className="font-medium text-fg hover:text-accent hover:underline"
            >
              {repo.full_name}
            </Link>
            {repo.is_private && <span className="text-xs text-muted">private</span>}
          </div>
          <p className="mt-0.5 truncate text-sm text-muted">
            {run ? (
              <>
                Latest review:{' '}
                <Link
                  to={`/pulls/${run.pull_request_id}`}
                  className="text-fg/90 hover:text-accent hover:underline"
                >
                  #{run.pull_number} {run.pull_title}
                </Link>{' '}
                · {timeAgo(run.created_at)}
              </>
            ) : (
              'No reviews yet'
            )}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 pl-11 sm:pl-0">
        {run && <RunStatusBadge status={run.status} />}
        <IndexBadge status={repo.index_status} />
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
      <PageHeader
        title={`Welcome, ${me.user.name ?? me.user.login}`}
        meta="Your repositories and the latest CodeLens review on each."
      />

      <section aria-labelledby="installations-heading">
        <h2
          id="installations-heading"
          className="mb-3 text-xs font-medium tracking-wide text-muted uppercase"
        >
          Your installations
        </h2>
        {me.installations.length === 0 ? (
          <EmptyState
            title="No installations yet"
            icon={<Building2 />}
            action={
              <a href={GITHUB_INSTALLATIONS_URL} className={buttonStyles({ variant: 'primary' })}>
                Manage GitHub App installations
                <ExternalLink aria-hidden="true" />
              </a>
            }
          >
            Install the CodeLens GitHub App on an account or organisation, then sign in again.
          </EmptyState>
        ) : (
          <ul className="flex flex-wrap gap-2">
            {me.installations.map((inst) => (
              <li
                key={inst.id}
                className="flex items-center gap-2.5 rounded-lg border border-line bg-surface py-2 pr-2.5 pl-3 shadow-card"
              >
                <span aria-hidden="true" className="text-muted [&_svg]:size-4">
                  {inst.account_type === 'organization' ? <Building2 /> : <User />}
                </span>
                <span className="text-sm font-medium text-fg">{inst.account_login}</span>
                <span className="text-xs text-muted">({inst.account_type})</span>
                <Badge tone={inst.role === 'member' ? 'neutral' : 'accent'}>
                  {ROLE_LABEL[inst.role]}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="repos-heading" className="mt-10">
        <Card>
          <CardHeader
            title="Repositories"
            titleId="repos-heading"
            description={repos.data ? `${repos.data.total} visible to you` : undefined}
            actions={
              <label className="relative block">
                <span className="sr-only">Search repositories</span>
                <Search
                  aria-hidden="true"
                  className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted"
                />
                <Input
                  type="search"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value)
                    setPage(0)
                  }}
                  placeholder="Search repositories"
                  className="w-56 pl-8"
                />
              </label>
            }
          />
          <div className="p-3">
            {repos.isPending ? (
              <ListSkeleton label="Loading repositories…" />
            ) : repos.isError ? (
              <ErrorState error={repos.error} onRetry={() => void repos.refetch()} />
            ) : repos.data.items.length === 0 ? (
              <EmptyState
                title={q ? 'No repositories match your search' : 'No repositories yet'}
                icon={q ? <Search /> : <FolderGit2 />}
                action={
                  q ? (
                    <Button onClick={() => setSearch('')}>Clear search</Button>
                  ) : (
                    <a href={GITHUB_INSTALLATIONS_URL} className={buttonStyles()}>
                      Choose repositories on GitHub
                      <ExternalLink aria-hidden="true" />
                    </a>
                  )
                }
              >
                {q
                  ? 'Try a different name.'
                  : 'Repositories appear here once the app is installed on them and you have access.'}
              </EmptyState>
            ) : (
              <>
                <ul className="divide-y divide-line overflow-hidden rounded-md border border-line">
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
        </Card>
      </section>
    </div>
  )
}
