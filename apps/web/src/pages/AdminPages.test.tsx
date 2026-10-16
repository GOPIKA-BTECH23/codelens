import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../auth/AuthProvider'
import { ToastProvider } from '../components/ToastProvider'
import { parseIgnorePaths } from '../lib/ignorePaths'
import { json, mockApi } from '../test/fetchMock'
import { AnalyticsPage } from './AnalyticsPage'
import { RepoPage } from './RepoPage'

const meWithRole = (role: string) => ({
  user: { id: 'u1', login: 'alice', name: null, avatar_url: null },
  installations: [
    {
      id: 'i1',
      github_installation_id: 1,
      account_login: 'acme',
      account_type: 'organization',
      role,
    },
  ],
})

const repo = (role: string) => ({
  id: 'r1',
  installation_id: 'i1',
  installation_login: 'acme',
  full_name: 'acme/api',
  default_branch: 'main',
  is_private: false,
  index_status: 'not_indexed',
  last_indexed_sha: null,
  role,
  latest_run: null,
  settings: { enabled_agents: ['security'], severity_threshold: 'info', ignore_paths: [] },
})
const NO_PULLS = () => json(200, { items: [], total: 0, limit: 20, offset: 0 })

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[path]}>
          <AuthProvider>
            <Routes>
              <Route path="/repos/:repoId" element={<RepoPage />} />
              <Route path="/analytics" element={<AnalyticsPage />} />
            </Routes>
          </AuthProvider>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

describe('repository settings', () => {
  it('are hidden from plain members', async () => {
    mockApi({
      'GET /api/me': () => json(200, meWithRole('member')),
      'GET /api/repos/r1': () => json(200, repo('member')),
      'GET /api/repos/r1/pulls?limit=20&offset=0': NO_PULLS,
    })
    renderAt('/repos/r1')

    await screen.findByRole('heading', { name: 'acme/api' })
    expect(screen.queryByText('Review settings')).not.toBeInTheDocument()
  })

  it('let an admin save a partial change and show a toast', async () => {
    let body: unknown = null
    mockApi({
      'GET /api/me': () => json(200, meWithRole('admin')),
      'GET /api/repos/r1': () => json(200, repo('admin')),
      'GET /api/repos/r1/pulls?limit=20&offset=0': NO_PULLS,
      'PATCH /api/repos/r1/settings': (init) => {
        body = JSON.parse(String(init?.body))
        return json(200, body)
      },
    })
    renderAt('/repos/r1')

    await userEvent.selectOptions(await screen.findByLabelText('Minimum severity to post'), 'high')
    await userEvent.type(screen.getByLabelText(/Ignore paths/), 'docs/**{Enter}vendor/**')
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }))

    expect(await screen.findByText('Settings saved')).toBeInTheDocument()
    expect(body).toEqual({
      enabled_agents: ['security'],
      severity_threshold: 'high',
      ignore_paths: ['docs/**', 'vendor/**'],
    })
  })

  it('show the API error when saving fails', async () => {
    mockApi({
      'GET /api/me': () => json(200, meWithRole('admin')),
      'GET /api/repos/r1': () => json(200, repo('admin')),
      'GET /api/repos/r1/pulls?limit=20&offset=0': NO_PULLS,
      'PATCH /api/repos/r1/settings': () =>
        json(403, { error: { code: 'forbidden', message: 'Requires the admin role or higher' } }),
    })
    renderAt('/repos/r1')

    await userEvent.click(await screen.findByRole('button', { name: 'Save settings' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Requires the admin role or higher')
  })

  it('parseIgnorePaths trims, drops blanks and duplicates', () => {
    expect(parseIgnorePaths(' a/** \n\nb\na/**\n')).toEqual(['a/**', 'b'])
  })
})

describe('analytics page', () => {
  afterEach(() => vi.useRealTimers())

  const day = (d: string, runs: number, cost: number, rate: number | null) => ({
    day: d,
    runs,
    cost_usd: cost,
    helpful: rate === null ? 0 : 1,
    not_helpful: 0,
    acceptance_rate: rate,
  })
  const usage = {
    date_from: '2026-10-16',
    date_to: '2026-10-17',
    totals: {
      runs: 3,
      succeeded: 2,
      failed: 1,
      skipped: 0,
      cost_usd: 0.5,
      tokens_in: 100,
      tokens_out: 50,
      latency_p50_ms: 1500,
      latency_p95_ms: 4000,
      findings: 4,
      helpful: 1,
      not_helpful: 1,
      acceptance_rate: 0.5,
    },
    days: [day('2026-10-16', 1, 0.2, null), day('2026-10-17', 2, 0.3, 0.5)],
  }
  it('shows totals, three charts and a table view', async () => {
    // Fix "today" so the requested range, and therefore the mocked URL, is deterministic.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-10-17T12:00:00Z'))
    mockApi({
      'GET /api/me': () => json(200, meWithRole('owner')),
      'GET /api/analytics/usage?from=2026-09-18&to=2026-10-17': () => json(200, usage),
    })
    renderAt('/analytics')

    expect(await screen.findByText('Acceptance rate')).toBeInTheDocument()
    expect(screen.getByText('50%', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText('1.5 s / 4.0 s')).toBeInTheDocument()
    expect(screen.getByText(/Cost is an estimate/)).toBeInTheDocument()
    expect(screen.getAllByRole('figure')).toHaveLength(3)
    const runsChart = screen.getByRole('group', { name: 'Runs per day' })
    expect(within(runsChart).getByRole('img', { name: /2 runs/ })).toBeInTheDocument()
    vi.useRealTimers()
  })

  it('tells non-admins the page is not for them without calling the API', async () => {
    const spy = mockApi({ 'GET /api/me': () => json(200, meWithRole('member')) })
    renderAt('/analytics')

    expect(await screen.findByText('Analytics are for admins')).toBeInTheDocument()
    await waitFor(() => expect(spy.mock.calls.map(([u]) => String(u)).join()).not.toMatch(/usage/))
  })
})
