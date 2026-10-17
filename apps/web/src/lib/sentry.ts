import type { ErrorEvent } from '@sentry/react'

/**
 * Strip anything that identifies the user or carries page data before an error leaves the
 * browser: cookies, the query string (the OAuth return carries a one-time code), user info and
 * breadcrumbs (they can hold clicked text and fetched URLs).
 */
export function scrubEvent(event: ErrorEvent): ErrorEvent {
  if (event.request) {
    delete event.request.cookies
    delete event.request.query_string
    delete event.request.data
    delete event.request.headers
    if (event.request.url) event.request.url = event.request.url.split(/[?#]/)[0]
  }
  delete event.user
  delete event.breadcrumbs
  return event
}

/**
 * Start Sentry only when VITE_SENTRY_DSN was set at build time. The SDK is loaded with a
 * dynamic import, so a build without a DSN downloads none of it.
 */
export async function initSentry(): Promise<boolean> {
  const dsn = import.meta.env.VITE_SENTRY_DSN
  if (!dsn) return false
  const Sentry = await import('@sentry/react')
  Sentry.init({
    dsn,
    environment: import.meta.env.MODE,
    // v11 collects user info, cookies, headers, bodies and query strings unless told not to.
    dataCollection: {
      userInfo: false,
      cookies: false,
      httpHeaders: false,
      httpBodies: [],
      urlQueryParams: false,
    },
    tracesSampleRate: 0,
    beforeSend: scrubEvent,
  })
  return true
}
