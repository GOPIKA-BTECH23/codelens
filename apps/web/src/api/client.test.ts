import { describe, expect, it } from 'vitest'
import { callsTo, json, mockApi } from '../test/fetchMock'
import { ApiError, api } from './client'

const ME = {
  user: { id: 'u1', login: 'alice', name: 'Alice', avatar_url: null },
  installations: [],
}
const EXPIRED = () =>
  json(401, { error: { code: 'token_expired', message: 'Access token expired' } })

describe('api client', () => {
  it('sends the CSRF header and same-origin credentials on every request', async () => {
    const fetchSpy = mockApi({ 'POST /api/auth/logout': () => json(204) })

    await api.logout()

    const init = fetchSpy.mock.calls[0]?.[1]
    expect(init?.credentials).toBe('same-origin')
    expect(init?.headers).toMatchObject({ 'X-Requested-With': 'codelens' })
  })

  it('refreshes once on 401 and replays the request', async () => {
    const fetchSpy = mockApi({
      'GET /api/me': [EXPIRED, () => json(200, ME)],
      'POST /api/auth/refresh': () => json(204),
    })

    await expect(api.me()).resolves.toEqual(ME)
    expect(callsTo(fetchSpy, 'POST /api/auth/refresh')).toBe(1)
    expect(callsTo(fetchSpy, 'GET /api/me')).toBe(2)
  })

  it('shares one refresh between parallel requests (single-flight)', async () => {
    let releaseRefresh!: () => void
    const refreshGate = new Promise<void>((resolve) => (releaseRefresh = resolve))
    let refreshed = false
    const fetchSpy = mockApi({
      'GET /api/me': () => (refreshed ? json(200, ME) : EXPIRED()),
      'POST /api/auth/refresh': async () => {
        await refreshGate // hold the refresh open so all three 401s pile up behind it
        refreshed = true
        return json(204)
      },
    })

    const results = Promise.all([api.me(), api.me(), api.me()])
    await new Promise((resolve) => setTimeout(resolve, 0))
    releaseRefresh()

    await expect(results).resolves.toEqual([ME, ME, ME])
    expect(callsTo(fetchSpy, 'POST /api/auth/refresh')).toBe(1)
  })

  it('gives up with the original 401 when the refresh fails', async () => {
    const fetchSpy = mockApi({
      'GET /api/me': EXPIRED,
      'POST /api/auth/refresh': () =>
        json(401, { error: { code: 'invalid_refresh_token', message: 'x' } }),
    })

    const error = await api.me().catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(401)
    expect(callsTo(fetchSpy, 'POST /api/auth/refresh')).toBe(1)
    expect(callsTo(fetchSpy, 'GET /api/me')).toBe(1) // no replay, no loop
  })

  it('never tries to refresh when logout itself returns 401', async () => {
    const fetchSpy = mockApi({ 'POST /api/auth/logout': () => json(401) })

    await expect(api.logout()).rejects.toBeInstanceOf(ApiError)
    expect(callsTo(fetchSpy, 'POST /api/auth/refresh')).toBe(0)
  })

  it('a second refresh round starts after the first one finished', async () => {
    const fetchSpy = mockApi({
      'GET /api/me': [EXPIRED, () => json(200, ME), EXPIRED, () => json(200, ME)],
      'POST /api/auth/refresh': () => json(204),
    })

    await api.me()
    await api.me()

    expect(callsTo(fetchSpy, 'POST /api/auth/refresh')).toBe(2)
  })
})
