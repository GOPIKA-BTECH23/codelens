import { vi } from 'vitest'

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>

export function json(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status })
}

/**
 * Replace fetch with a tiny router: keys are "METHOD /path". A value may be one handler or
 * a list consumed in order (the last one repeats). Returns the spy so tests can count calls.
 */
export function mockApi(routes: Record<string, Handler | Handler[]>) {
  const queues = new Map(
    Object.entries(routes).map(([key, value]) => [
      key,
      Array.isArray(value) ? [...value] : [value],
    ]),
  )
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    const queue = queues.get(key)
    if (!queue) throw new Error(`unexpected request: ${key}`)
    // The last handler stays in the queue and answers every later call.
    const handler = queue.length > 1 ? queue.shift() : queue[0]
    if (!handler) throw new Error(`no handler left for: ${key}`)
    return handler(init)
  })
}

export function callsTo(spy: ReturnType<typeof mockApi>, key: string): number {
  return spy.mock.calls.filter(
    ([input, init]) => `${init?.method ?? 'GET'} ${String(input)}` === key,
  ).length
}
