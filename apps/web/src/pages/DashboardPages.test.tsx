import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { describe, expect, it } from 'vitest'
import { AuthProvider } from '../auth/AuthProvider'
import { ToastProvider } from '../components/ToastProvider'
import { json, mockApi } from '../test/fetchMock'
import { DashboardPage } from './DashboardPage'
import { RepoPage } from './RepoPage'

const ME = {
  user: { id: 'u1', login: 'alice', name: null, avatar_url: null },
  installations: [
    {
      id: 'i1',
      github_installation_id: 1,
      account_login: 'acme',
      account_type: 'organization',
      role: 'member',
    },
  ],
}

const REPO = {
  id: 'r1',
  installation_id: 'i1',
  installation_login: 'acme',
  full_name: 'acme/api',
  default_branch: 'main',
  is_private: true,
  index_status: 'failed',
  last_indexed_sha: null,
  role: 'member',
  latest_run: {
    id: 'run1',
    status: 'succeeded',
    created_at: new Date().toISOString(),
    pull_request_id: 'p1',
    pull_number: 7,
    pull_title: 'Add lookup',
  },
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <AuthProvider>
            <Routes>
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/repos/:repoId" element={<RepoPage />} />
            </Routes>
          </AuthProvider>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

const page = <T,>(items: T[], total = items.length) => ({ items, total, limit: 20, offset: 0 })

describe('dashboard', () => {
  it('lists repositories with their latest review and says indexing is not available yet', async () => {
    mockApi({
      'GET /api/me': () => json(200, ME),
      'GET /api/repos?limit=20&offset=0': () => json(200, page([REPO])),
    })
    renderAt('/dashboard')

    const link = await screen.findByRole('link', { name: 'acme/api' })
    expect(link).toHaveAttribute('href', '/repos/r1')
    const row = link.closest('li') as HTMLElement
    expect(within(row).getByRole('link', { name: '#7 Add lookup' })).toHaveAttribute(
      'href',
      '/pulls/p1',
    )
    expect(within(row).getByText('Reviewed')).toBeInTheDocument()
    // `failed` is what the M5 stub sets; the UI must not show it as a user-fixable error.
    expect(within(row).getByText('Indexing: not available yet')).toBeInTheDocument()
    expect(within(row).queryByText(/failed/i)).not.toBeInTheDocument()
  })

  it('shows an empty state when there are no repositories', async () => {
    mockApi({
      'GET /api/me': () => json(200, ME),
      'GET /api/repos?limit=20&offset=0': () => json(200, page([])),
    })
    renderAt('/dashboard')

    expect(await screen.findByText('No repositories yet')).toBeInTheDocument()
  })

  it('shows the error and retries', async () => {
    mockApi({
      'GET /api/me': () => json(200, ME),
      'GET /api/repos?limit=20&offset=0': [
        () => json(500, { error: { code: 'internal_error', message: 'Boom' } }),
        () => json(200, page([REPO])),
      ],
    })
    renderAt('/dashboard')

    expect(await screen.findByText('Boom')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('link', { name: 'acme/api' })).toBeInTheDocument()
  })

  it('pages through repositories', async () => {
    mockApi({
      'GET /api/me': () => json(200, ME),
      'GET /api/repos?limit=20&offset=0': () => json(200, page([REPO], 21)),
      'GET /api/repos?limit=20&offset=20': () =>
        json(200, page([{ ...REPO, id: 'r2', full_name: 'acme/web' }], 21)),
    })
    renderAt('/dashboard')
    await screen.findByRole('link', { name: 'acme/api' })

    await userEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(await screen.findByRole('link', { name: 'acme/web' })).toBeInTheDocument()
    expect(screen.getByText('21–21 of 21')).toBeInTheDocument()
  })
})

describe('repo page', () => {
  const pull = (n: number, state: string, run: object | null) => ({
    id: `p${n}`,
    repository_id: 'r1',
    number: n,
    title: `PR ${n}`,
    author_login: 'dev',
    state,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    latest_run: run,
  })

  it('shows pull requests with state and review badges, and filters by state', async () => {
    const reviewed = {
      id: 'x',
      status: 'succeeded',
      created_at: '2026-10-17T10:00:00Z',
      findings_count: 2,
    }
    mockApi({
      'GET /api/me': () => json(200, ME),
      'GET /api/repos/r1': () => json(200, { ...REPO, settings: {} }),
      'GET /api/repos/r1/pulls?limit=20&offset=0': () =>
        json(200, page([pull(1, 'open', reviewed), pull(2, 'merged', null)])),
      'GET /api/repos/r1/pulls?state=merged&limit=20&offset=0': () =>
        json(200, page([pull(2, 'merged', null)])),
    })
    renderAt('/repos/r1')

    const first = (await screen.findByRole('link', { name: '#1 PR 1' })).closest(
      'li',
    ) as HTMLElement
    expect(within(first).getByText('Open')).toBeInTheDocument()
    expect(within(first).getByText('Reviewed')).toBeInTheDocument()
    expect(within(first).getByText('2 findings')).toBeInTheDocument()
    const second = screen.getByRole('link', { name: '#2 PR 2' }).closest('li') as HTMLElement
    expect(within(second).getByText('Not reviewed')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('State'), 'merged')

    await screen.findByRole('link', { name: '#2 PR 2' })
    expect(screen.queryByRole('link', { name: '#1 PR 1' })).not.toBeInTheDocument()
  })

  it('shows an error when the repository is not visible (404)', async () => {
    mockApi({
      'GET /api/me': () => json(200, ME),
      'GET /api/repos/r1': () =>
        json(404, { error: { code: 'not_found', message: 'Repository not found' } }),
      'GET /api/repos/r1/pulls?limit=20&offset=0': () =>
        json(404, { error: { code: 'not_found', message: 'Repository not found' } }),
    })
    renderAt('/repos/r1')

    expect(await screen.findByRole('alert')).toHaveTextContent('Repository not found')
  })
})
