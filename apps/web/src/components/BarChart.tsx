import { niceMax } from '../lib/chart'
import { Card, CardHeader } from './ui/Card'

export interface BarDatum {
  label: string // x label, e.g. "Oct 17"
  value: number | null // null = no data that day (no bar is drawn)
  text: string // formatted value for the tooltip and the table
}

interface Props {
  title: string
  description?: string
  data: BarDatum[]
  /** Top of the y axis. Defaults to a rounded-up maximum of the data. */
  max?: number
  formatAxis: (value: number) => string
}

const WIDTH = 640
const HEIGHT = 180
const PAD = { top: 12, right: 8, bottom: 24, left: 52 }

/**
 * A small single-series bar chart in plain SVG (one measure per chart, so no legend: the
 * title names it). Bars have a rounded top and sit on the baseline; the grid is recessive.
 * Every bar is keyboard-focusable and has a tooltip; a data table is one click away so the
 * numbers never depend on seeing the colour or the shape.
 */
export function BarChart({ title, description, data, max, formatAxis }: Props) {
  const top = max ?? niceMax(Math.max(0, ...data.map((d) => d.value ?? 0)))
  const plotW = WIDTH - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const slot = plotW / Math.max(1, data.length)
  const barW = Math.max(2, Math.min(24, slot - 2)) // thin marks, 2px gap
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH
  const ticks = [0, top / 2, top]
  const labelled = [0, Math.floor((data.length - 1) / 2), data.length - 1]

  return (
    <Card>
      <figure>
        <figcaption>
          <CardHeader title={title} description={description} as="h3" />
        </figcaption>
        <div className="px-4 pt-4">
          <svg
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            role="group"
            aria-label={title}
            className="h-auto w-full overflow-visible"
          >
            {ticks.map((t) => (
              <g key={t}>
                <line
                  x1={PAD.left}
                  x2={WIDTH - PAD.right}
                  y1={y(t)}
                  y2={y(t)}
                  className={t === 0 ? 'stroke-line-strong' : 'stroke-line'}
                  strokeDasharray={t === 0 ? undefined : '2 4'}
                />
                <text
                  x={PAD.left - 8}
                  y={y(t) + 3.5}
                  textAnchor="end"
                  className="fill-muted text-[10px] tabular-nums"
                >
                  {formatAxis(t)}
                </text>
              </g>
            ))}
            {data.map((d, i) => {
              if (d.value === null) return null
              const h = Math.max(d.value > 0 ? 2 : 0, (d.value / top) * plotH)
              const x = PAD.left + i * slot + (slot - barW) / 2
              return (
                <rect
                  key={d.label}
                  x={x}
                  y={PAD.top + plotH - h}
                  width={barW}
                  height={h}
                  rx={Math.min(3, barW / 2)}
                  tabIndex={0}
                  role="img"
                  aria-label={`${d.label}: ${d.text}`}
                  className="fill-chart transition-opacity duration-150 hover:opacity-75 focus:opacity-75"
                >
                  <title>{`${d.label}: ${d.text}`}</title>
                </rect>
              )
            })}
            {[...new Set(labelled)].map((i, k) => {
              const d = data[i]
              if (!d) return null
              const x = PAD.left + i * slot + slot / 2
              const anchor = k === 0 ? 'start' : i === data.length - 1 ? 'end' : 'middle'
              return (
                <text
                  key={d.label}
                  x={anchor === 'start' ? PAD.left : anchor === 'end' ? WIDTH - PAD.right : x}
                  y={HEIGHT - 6}
                  textAnchor={anchor}
                  className="fill-muted text-[10px]"
                >
                  {d.label}
                </text>
              )
            })}
          </svg>
        </div>
        <details className="border-t border-line px-4 py-2.5 text-sm">
          <summary className="cursor-pointer text-xs text-muted select-none hover:text-fg">
            Show as table
          </summary>
          <table className="mt-2 w-full text-left text-xs">
            <thead>
              <tr className="text-muted">
                <th className="py-1 font-medium">Day</th>
                <th className="py-1 font-medium">Value</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.label} className="border-t border-line">
                  <td className="py-1">{d.label}</td>
                  <td className="py-1 tabular-nums">{d.value === null ? 'no data' : d.text}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      </figure>
    </Card>
  )
}
