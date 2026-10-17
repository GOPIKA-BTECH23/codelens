import type { ErrorEvent } from '@sentry/react'
import { describe, expect, it } from 'vitest'
import { initSentry, scrubEvent } from './sentry'

describe('scrubEvent', () => {
  it('removes cookies, query strings, user and breadcrumbs but keeps the error', () => {
    const event = {
      type: undefined,
      exception: { values: [{ type: 'TypeError', value: 'x is undefined' }] },
      request: {
        url: 'https://codelens.example/auth/callback?code=abc&state=def#frag',
        cookies: { cl_access: 'jwt' },
        query_string: 'code=abc',
        headers: { Cookie: 'cl_access=jwt' },
      },
      user: { ip_address: '1.2.3.4' },
      breadcrumbs: [{ message: 'clicked' }],
    } as ErrorEvent

    const out = scrubEvent(event)

    expect(out.request?.url).toBe('https://codelens.example/auth/callback')
    expect(out.request?.cookies).toBeUndefined()
    expect(out.request?.query_string).toBeUndefined()
    expect(out.request?.headers).toBeUndefined()
    expect(out.user).toBeUndefined()
    expect(out.breadcrumbs).toBeUndefined()
    expect(out.exception?.values?.[0]?.type).toBe('TypeError')
  })
})

describe('initSentry', () => {
  it('stays off without VITE_SENTRY_DSN', async () => {
    expect(await initSentry()).toBe(false)
  })
})
