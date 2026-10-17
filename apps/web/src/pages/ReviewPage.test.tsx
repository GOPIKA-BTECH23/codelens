import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { describe, expect, it } from 'vitest'
import type { FindingOut, ReviewOut } from '../api/types'
import { ToastProvider } from '../components/ToastProvider'
import { callsTo, json, mockApi } from '../test/fetchMock'
import { ReviewPage } from './ReviewPage'

const PULL = {
  id: 'p1',
  repository_id: 'r1',
  number: 7,
  title: 'Add user lookup',
  author_login: 'dev',
  state: 'open',
  created_at: '2026-10-17T10:00:00Z',
  updated_at: '2026-10-17T10:00:00Z',
  latest_run: null,
}
const REPO = { id: 'r1', full_name: 'acme/api' }

function finding(overrides: Partial<FindingOut> = {}): FindingOut {
  return {
    id: 'f1',
    file: 'app/db.py',
    line: 3,
    end_line: null,
    severity: 'high',
    category: 'sql-injection',
    message: 'User input is concatenated into the query.',
    suggestion: 'Use a parameterised query.\ncursor.execute(sql, (uid,))',
    confidence: 0.9,
    user_feedback: null,
    dismissed: false,
    ...overrides,
  }
}

function reviewFixture(findings: FindingOut[], patchAvailable = true): ReviewOut {
  return {
    run: {
      id: 'run1',
      status: 'succeeded',
      model: 'openai/gpt-oss-120b',
      prompt_version: 'security_v1',
      tokens_in: 1200,
      tokens_out: 300,
      cost_usd: 0.0042,
      latency_ms: 2500,
      head_sha: 'abcdef1234567890',
      error: null,
      created_at: '2026-10-17T10:05:00Z',
      finished_at: '2026-10-17T10:05:03Z',
      review_posted: true,
    },
    summary: { what_changed: 'Adds a user lookup helper.', notes: ['Reviewed 1 file(s).'] },
    findings,
    files: [
      {
        path: 'app/db.py',
        old_path: null,
        status: 'modified',
        additions: 2,
        deletions: 1,
        patch_available: patchAvailable,
        hunks: patchAvailable
          ? [
              {
                header: '@@ -1,2 +1,3 @@',
                lines: [
                  { kind: ' ', old_line: 1, new_line: 1, text: 'import db' },
                  { kind: '-', old_line: 2, new_line: null, text: 'q = "old"' },
                  { kind: '+', old_line: null, new_line: 2, text: 'uid = request.args["id"]' },
                  { kind: '+', old_line: null, new_line: 3, text: 'db.execute(f"select {uid}")' },
                ],
              },
            ]
          : [],
      },
    ],
  }
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={['/pulls/p1']}>
          <Routes>
            <Route path="/pulls/:pullId" element={<ReviewPage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

/** Mock the three reads. `state.review` is what the server currently holds. */
function serve(state: { review: ReviewOut | null }, extra: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    'GET /api/pulls/p1': () => json(200, PULL),
    'GET /api/repos/r1': () => json(200, REPO),
    'GET /api/pulls/p1/review': () => json(200, state.review),
    ...extra,
  })
}

describe('review page', () => {
  it('shows the summary, run metadata and the finding under the line it points at', async () => {
    serve({ review: reviewFixture([finding()]) })
    renderPage()

    expect(await screen.findByRole('heading', { name: '#7 Add user lookup' })).toBeInTheDocument()
    expect(screen.getByText('Adds a user lookup helper.')).toBeInTheDocument()
    // Run metadata: model, prompt version, latency, ESTIMATED cost.
    expect(screen.getByText('openai/gpt-oss-120b')).toBeInTheDocument()
    expect(screen.getByText('security_v1')).toBeInTheDocument()
    expect(screen.getByText('2.5 s')).toBeInTheDocument()
    expect(screen.getByText('Estimated cost')).toBeInTheDocument()
    expect(screen.getByText('$0.0042')).toBeInTheDocument()

    // The finding card sits inside the diff table, in the row right after line 3.
    const card = screen.getByRole('article', { name: /high finding: sql-injection in app\/db.py/ })
    const table = card.closest('table')
    expect(table).not.toBeNull()
    const rows = within(table as HTMLElement).getAllByRole('row')
    const lineRow = rows.findIndex((row) =>
      row.textContent?.includes('db.execute(f"select {uid}")'),
    )
    expect(rows[lineRow + 1]).toContainElement(card)
  })

  it('renders prose as plain text and only code in a code block', async () => {
    serve({ review: reviewFixture([finding()]) })
    renderPage()

    const card = await screen.findByRole('article')
    const code = within(card).getByText('cursor.execute(sql, (uid,))')
    expect(code.tagName).toBe('CODE')
    const prose = within(card).getByText('Use a parameterised query.')
    expect(prose.closest('pre')).toBeNull()
    expect(prose.tagName).toBe('P')
  })

  it('never renders model text as HTML', async () => {
    serve({ review: reviewFixture([finding({ message: '<img src=x onerror=alert(1)> bad' })]) })
    renderPage()

    const card = await screen.findByRole('article')
    expect(card.querySelector('img')).toBeNull()
    expect(within(card).getByText(/<img src=x onerror=alert\(1\)> bad/)).toBeInTheDocument()
  })

  it('marks a finding useful immediately, before the server answers', async () => {
    const state = { review: reviewFixture([finding()]) }
    let release: () => void = () => undefined
    const spy = serve(state, {
      'PATCH /api/findings/f1': (init) =>
        new Promise<Response>((resolve) => {
          release = () => {
            state.review = reviewFixture([finding({ user_feedback: 'helpful' })])
            resolve(json(200, finding({ user_feedback: 'helpful' })))
          }
          expect(JSON.parse(String(init?.body))).toEqual({ user_feedback: 'helpful' })
        }),
    })
    renderPage()
    const useful = await screen.findByRole('button', { name: 'Useful' })
    expect(useful).toHaveAttribute('aria-pressed', 'false')

    await userEvent.click(useful)

    // The PATCH has not finished (release() was not called), yet the button already changed.
    expect(screen.getByRole('button', { name: 'Useful' })).toHaveAttribute('aria-pressed', 'true')
    expect(callsTo(spy, 'PATCH /api/findings/f1')).toBe(1)

    release()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Useful' })).toHaveAttribute(
        'aria-pressed',
        'true',
      ),
    )
    // After settling, the page refetched the review so the cache equals the server.
    await waitFor(() => expect(callsTo(spy, 'GET /api/pulls/p1/review')).toBe(2))
  })

  it('pressing the active button again clears the vote', async () => {
    const state = { review: reviewFixture([finding({ user_feedback: 'helpful' })]) }
    serve(state, {
      'PATCH /api/findings/f1': (init) => {
        expect(JSON.parse(String(init?.body))).toEqual({ user_feedback: null })
        state.review = reviewFixture([finding()])
        return json(200, finding())
      },
    })
    renderPage()
    const useful = await screen.findByRole('button', { name: 'Useful' })
    expect(useful).toHaveAttribute('aria-pressed', 'true')

    await userEvent.click(useful)

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Useful' })).toHaveAttribute(
        'aria-pressed',
        'false',
      ),
    )
  })

  it('rolls the change back and shows a toast when the server rejects it', async () => {
    const state = { review: reviewFixture([finding()]) }
    serve(state, {
      'PATCH /api/findings/f1': () =>
        json(500, { error: { code: 'internal_error', message: 'Database exploded' } }),
    })
    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: 'Not useful' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not save your change: Database exploded')
    expect(screen.getByRole('button', { name: 'Not useful' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('a failed change on one finding does not undo another finding that succeeded', async () => {
    const a = finding({ id: 'f1', line: 2, category: 'a-issue' })
    const b = finding({ id: 'f2', line: 3, category: 'b-issue' })
    const state = { review: reviewFixture([a, b]) }
    serve(state, {
      'PATCH /api/findings/f1': () => json(500, { error: { code: 'x', message: 'nope' } }),
      'PATCH /api/findings/f2': () => {
        state.review = reviewFixture([a, { ...b, user_feedback: 'helpful' }])
        return json(200, { ...b, user_feedback: 'helpful' })
      },
    })
    renderPage()
    const cards = await screen.findAllByRole('article')
    const [first, second] = cards
    if (!first || !second) throw new Error('expected two findings')

    await userEvent.click(within(second).getByRole('button', { name: 'Useful' }))
    await userEvent.click(within(first).getByRole('button', { name: 'Useful' }))

    await screen.findByRole('alert')
    await waitFor(() => {
      const [x, y] = screen.getAllByRole('article')
      expect(within(x as HTMLElement).getByRole('button', { name: 'Useful' })).toHaveAttribute(
        'aria-pressed',
        'false',
      )
      expect(within(y as HTMLElement).getByRole('button', { name: 'Useful' })).toHaveAttribute(
        'aria-pressed',
        'true',
      )
    })
  })

  it('dismisses a finding and can show it again', async () => {
    const state = { review: reviewFixture([finding()]) }
    serve(state, {
      'PATCH /api/findings/f1': () => {
        state.review = reviewFixture([finding({ dismissed: true })])
        return json(200, finding({ dismissed: true }))
      },
    })
    renderPage()

    await userEvent.click(await screen.findByRole('button', { name: 'Dismiss' }))

    await waitFor(() => expect(screen.queryByRole('article')).not.toBeInTheDocument())
    await userEvent.click(await screen.findByRole('checkbox', { name: /Show dismissed \(1\)/ }))
    expect(await screen.findByRole('article')).toHaveTextContent('Dismissed')
    expect(screen.getByRole('button', { name: 'Restore' })).toBeInTheDocument()
  })

  it('filters findings by severity', async () => {
    const high = finding({ id: 'f1', severity: 'high', category: 'sql-injection', line: 3 })
    const low = finding({ id: 'f2', severity: 'low', category: 'weak-hash', line: 2 })
    serve({ review: reviewFixture([high, low]) })
    renderPage()
    expect(await screen.findAllByRole('article')).toHaveLength(2)

    await userEvent.click(screen.getByRole('button', { name: /^low \(1\)$/ }))

    const remaining = screen.getAllByRole('article')
    expect(remaining).toHaveLength(1)
    expect(remaining[0]).toHaveAccessibleName(/weak-hash/)
    expect(screen.getByRole('button', { name: /^low \(1\)$/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('still lists findings when the diff was not stored', async () => {
    serve({ review: { ...reviewFixture([finding()], false), files: [] } })
    renderPage()

    expect(await screen.findByText(/diff was not stored/)).toBeInTheDocument()
    expect(screen.getByRole('article')).toBeInTheDocument()
  })

  it('shows an empty state for a pull request that was never reviewed', async () => {
    serve({ review: null })
    renderPage()

    expect(await screen.findByText('Not reviewed yet')).toBeInTheDocument()
  })

  it('shows the API error and lets the user retry', async () => {
    const spy = mockApi({
      'GET /api/pulls/p1': () => json(200, PULL),
      'GET /api/repos/r1': () => json(200, REPO),
      'GET /api/pulls/p1/review': [
        () => json(404, { error: { code: 'not_found', message: 'Pull request not found' } }),
        () => json(200, reviewFixture([finding()])),
      ],
    })
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Pull request not found')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('article')).toBeInTheDocument()
    expect(callsTo(spy, 'GET /api/pulls/p1/review')).toBe(2)
  })
})
