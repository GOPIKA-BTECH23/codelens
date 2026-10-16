import { useState } from 'react'
import { useUsage } from '../api/queries'
import { useAuth } from '../auth/context'
import { BarChart } from '../components/BarChart'
import { EmptyState, ErrorState, Loading } from '../components/States'
import { formatCost, formatLatency, formatPercent } from '../lib/format'
import { isAnyAdmin } from '../lib/roles'

const RANGES = [7, 30, 90] as const

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10)
}

function shortDay(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate-200 p-3 dark:border-slate-800">
      <dt className="text-sm text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold">{value}</dd>
    </div>
  )
}

/** Admin-only usage analytics. The API enforces the role; this page just explains the 403. */
export function AnalyticsPage() {
  const { me } = useAuth()
  const [days, setDays] = useState<(typeof RANGES)[number]>(30)
  const [now] = useState(() => Date.now()) // fixed per visit, so the query key is stable
  const to = isoDay(new Date(now))
  const from = isoDay(new Date(now - (days - 1) * 86_400_000))
  const allowed = isAnyAdmin(me)
  const usage = useUsage(from, to, allowed) // never ask the API when we know the answer is 403

  if (!allowed) {
    return (
      <EmptyState title="Analytics are for admins">
        You need the admin or owner role in an installation to see usage numbers.
      </EmptyState>
    )
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">Analytics</h1>
        <label className="text-sm">
          <span className="mr-2">Range</span>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value) as (typeof RANGES)[number])}
            className="rounded border border-slate-300 bg-transparent px-2 py-1.5 dark:border-slate-700 dark:bg-slate-950"
          >
            {RANGES.map((n) => (
              <option key={n} value={n}>
                Last {n} days
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
        Cost is an estimate at list price, not a bill. Days are UTC.
      </p>

      <div className="mt-6">
        {usage.isPending ? (
          <Loading label="Loading analytics…" />
        ) : usage.isError ? (
          <ErrorState error={usage.error} onRetry={() => void usage.refetch()} />
        ) : usage.data.totals.runs === 0 ? (
          <EmptyState title="No review runs in this range">
            Runs appear here after CodeLens reviews a pull request.
          </EmptyState>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Tile label="Runs" value={String(usage.data.totals.runs)} />
              <Tile label="Estimated cost" value={formatCost(usage.data.totals.cost_usd)} />
              <Tile
                label="Latency p50 / p95"
                value={`${formatLatency(usage.data.totals.latency_p50_ms)} / ${formatLatency(usage.data.totals.latency_p95_ms)}`}
              />
              <Tile
                label="Acceptance rate"
                value={formatPercent(usage.data.totals.acceptance_rate)}
              />
            </dl>
            <p className="mt-2 text-sm text-slate-600 dark:text-slate-400">
              {usage.data.totals.helpful} useful and {usage.data.totals.not_helpful} not useful
              votes on {usage.data.totals.findings} findings; {usage.data.totals.failed} failed and{' '}
              {usage.data.totals.skipped} skipped runs.
            </p>

            <div className="mt-6 grid gap-4 lg:grid-cols-1">
              <BarChart
                title="Runs per day"
                data={usage.data.days.map((d) => ({
                  label: shortDay(d.day),
                  value: d.runs,
                  text: `${d.runs} run${d.runs === 1 ? '' : 's'}`,
                }))}
                formatAxis={(v) => String(Math.round(v))}
              />
              <BarChart
                title="Estimated cost per day"
                data={usage.data.days.map((d) => ({
                  label: shortDay(d.day),
                  value: d.cost_usd,
                  text: formatCost(d.cost_usd),
                }))}
                formatAxis={(v) => formatCost(v)}
              />
              <BarChart
                title="Acceptance rate per day (days without votes are empty)"
                max={1}
                data={usage.data.days.map((d) => ({
                  label: shortDay(d.day),
                  value: d.acceptance_rate,
                  text: `${formatPercent(d.acceptance_rate)} (${d.helpful} useful, ${d.not_helpful} not useful)`,
                }))}
                formatAxis={(v) => formatPercent(v)}
              />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
