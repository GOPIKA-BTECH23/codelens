import { ChartColumn, Coins, Lock, ThumbsUp, Timer } from 'lucide-react'
import { type ReactNode, useState } from 'react'
import { useUsage } from '../api/queries'
import { useAuth } from '../auth/context'
import { BarChart } from '../components/BarChart'
import { PageHeader } from '../components/PageHeader'
import { EmptyState, ErrorState } from '../components/States'
import { Card } from '../components/ui/Card'
import { Select } from '../components/ui/Field'
import { PageSkeleton } from '../components/ui/Skeleton'
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

function Tile({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <Card className="p-4">
      <dt className="flex items-center gap-1.5 text-xs font-medium text-muted [&_svg]:size-3.5">
        <span aria-hidden="true" className="contents">
          {icon}
        </span>
        {label}
      </dt>
      <dd className="mt-2 text-2xl font-semibold tracking-tight text-fg tabular-nums">{value}</dd>
    </Card>
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
      <EmptyState title="Analytics are for admins" icon={<Lock />}>
        You need the admin or owner role in an installation to see usage numbers.
      </EmptyState>
    )
  }

  return (
    <div>
      <PageHeader
        title="Analytics"
        meta="Cost is an estimate at list price, not a bill. Days are UTC."
        actions={
          <label className="flex items-center gap-2 text-xs text-muted">
            <span>Range</span>
            <Select
              value={days}
              onChange={(e) => setDays(Number(e.target.value) as (typeof RANGES)[number])}
            >
              {RANGES.map((n) => (
                <option key={n} value={n}>
                  Last {n} days
                </option>
              ))}
            </Select>
          </label>
        }
      />

      {usage.isPending ? (
        <PageSkeleton label="Loading analytics…" />
      ) : usage.isError ? (
        <ErrorState error={usage.error} onRetry={() => void usage.refetch()} />
      ) : usage.data.totals.runs === 0 ? (
        <EmptyState title="No review runs in this range" icon={<ChartColumn />}>
          Runs appear here after CodeLens reviews a pull request.
        </EmptyState>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Tile icon={<ChartColumn />} label="Runs" value={String(usage.data.totals.runs)} />
            <Tile
              icon={<Coins />}
              label="Estimated cost"
              value={formatCost(usage.data.totals.cost_usd)}
            />
            <Tile
              icon={<Timer />}
              label="Latency p50 / p95"
              value={`${formatLatency(usage.data.totals.latency_p50_ms)} / ${formatLatency(usage.data.totals.latency_p95_ms)}`}
            />
            <Tile
              icon={<ThumbsUp />}
              label="Acceptance rate"
              value={formatPercent(usage.data.totals.acceptance_rate)}
            />
          </dl>
          <p className="mt-3 text-xs text-muted">
            {usage.data.totals.helpful} useful and {usage.data.totals.not_helpful} not useful votes
            on {usage.data.totals.findings} findings; {usage.data.totals.failed} failed and{' '}
            {usage.data.totals.skipped} skipped runs.
          </p>

          <div className="mt-6 grid gap-4 xl:grid-cols-2">
            <BarChart
              title="Runs per day"
              data={usage.data.days.map((d) => ({
                label: shortDay(d.day),
                value: d.runs,
                text: `${d.runs} run${d.runs === 1 ? '' : 's'}`,
              }))}
              formatAxis={(v) => (Number.isInteger(v) ? String(v) : v.toFixed(1))}
            />
            <BarChart
              title="Estimated cost per day"
              description="At list price; free-tier usage costs nothing real."
              data={usage.data.days.map((d) => ({
                label: shortDay(d.day),
                value: d.cost_usd,
                text: formatCost(d.cost_usd),
              }))}
              formatAxis={(v) => formatCost(v)}
            />
            <BarChart
              title="Acceptance rate per day"
              description="Useful ÷ (useful + not useful). Days without votes are empty."
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
  )
}
