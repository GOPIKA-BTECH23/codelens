import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HealthPage } from './HealthPage'

function renderPage() {
  // retry: false so a failing request surfaces immediately instead of after backoff delays.
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <HealthPage />
    </QueryClientProvider>,
  )
}

function mockFetchJson(status: number, body: unknown) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue(new Response(JSON.stringify(body), { status }))
}

describe('HealthPage', () => {
  it('shows the live /health response from the API', async () => {
    const fetchMock = mockFetchJson(200, {
      status: 'ok',
      version: '0.1.0',
      database: 'not_configured',
    })

    renderPage()

    expect(await screen.findByText('0.1.0')).toBeInTheDocument()
    expect(screen.getByText('not_configured')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/health', expect.anything())
  })

  it('shows the API error message when the request fails', async () => {
    mockFetchJson(500, { error: { code: 'internal_error', message: 'Internal server error' } })

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Internal server error')
  })
})
