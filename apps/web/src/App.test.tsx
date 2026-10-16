import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { App } from './App'
import { AuthProvider } from './auth/AuthProvider'
import { callsTo, json, mockApi } from './test/fetchMock'

const ME = {
  user: { id: 'u1', login: 'alice', name: 'Alice Anand', avatar_url: null },
  installations: [
    {
      id: 'i1',
      github_installation_id: 100,
      account_login: 'acme',
      account_type: 'organization',
      role: 'admin',
    },
  ],
}
const UNAUTHORIZED = () =>
  json(401, { error: { code: 'unauthorized', message: 'Authentication required' } })

function renderApp(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('auth flow in the UI', () => {
  it('sends a signed-out visitor from a protected page to the login page', async () => {
    mockApi({ 'GET /api/me': UNAUTHORIZED, 'POST /api/auth/refresh': UNAUTHORIZED })

    renderApp('/dashboard')

    const link = await screen.findByRole('link', { name: 'Sign in with GitHub' })
    // A full navigation to the API, which redirects to GitHub.
    expect(link).toHaveAttribute('href', '/api/auth/github/login')
  })

  it('shows the signed-in user from /me on the dashboard', async () => {
    mockApi({ 'GET /api/me': () => json(200, ME) })

    renderApp('/dashboard')

    expect(await screen.findByRole('heading', { name: 'Welcome, Alice Anand' })).toBeInTheDocument()
    expect(screen.getByText('acme')).toBeInTheDocument()
    expect(screen.getByText('Admin')).toBeInTheDocument()
  })

  it('stays signed in when the access token has expired (silent refresh)', async () => {
    const fetchSpy = mockApi({
      'GET /api/me': [UNAUTHORIZED, () => json(200, ME)],
      'POST /api/auth/refresh': () => json(204),
    })

    renderApp('/dashboard')

    expect(await screen.findByRole('heading', { name: /Welcome/ })).toBeInTheDocument()
    expect(callsTo(fetchSpy, 'POST /api/auth/refresh')).toBe(1)
  })

  it('signs out and returns to the login page', async () => {
    const fetchSpy = mockApi({
      'GET /api/me': () => json(200, ME),
      'POST /api/auth/logout': () => json(204),
    })
    renderApp('/dashboard')

    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('link', { name: 'Sign in with GitHub' })).toBeInTheDocument()
    expect(callsTo(fetchSpy, 'POST /api/auth/logout')).toBe(1)
  })

  it('explains a failed login', async () => {
    mockApi({ 'GET /api/me': UNAUTHORIZED, 'POST /api/auth/refresh': UNAUTHORIZED })

    renderApp('/login?error=invalid_state')

    expect(await screen.findByRole('alert')).toHaveTextContent('expired or was not started here')
  })

  it('shows an error instead of the login page when the API is down', async () => {
    mockApi({
      'GET /api/me': () =>
        json(503, { error: { code: 'service_unavailable', message: 'Database unavailable' } }),
    })

    renderApp('/dashboard')

    expect(await screen.findByRole('alert')).toHaveTextContent('Database unavailable')
    expect(screen.queryByRole('link', { name: 'Sign in with GitHub' })).not.toBeInTheDocument()
  })
})
