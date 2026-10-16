export interface BarDatum {
  label: string // x label, e.g. "Oct 17"
  value: number | null // null = no data that day (no bar is drawn)
  text: string // formatted value for the tooltip and the table
}

interface Props {
  title: string
  data: BarDatum[]
  /** Top of the y axis. Defaults to the largest value (at least 1). */
  max?: number
  formatAxis: (value: number) => string
}

const WIDTH = 640
const HEIGHT = 180
const PAD = { top: 12, right: 8, bottom: 24, left: 44 }

/**
 * A small single-series bar chart in plain SVG (one measure per chart, so no legend: the
 * title names it). Bars have a rounded top and sit on the baseline; the grid is a single
 * recessive line. Every bar is keyboard-focusable and has a tooltip; a data table is one
 * click away so the numbers never depend on seeing the colour or the shape.
 */
export function BarChart({ title, data, max, formatAxis }: Props) {
  const top = max ?? Math.max(1, ...data.map((d) => d.value ?? 0))
  const plotW = WIDTH - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom
  const slot = plotW / Math.max(1, data.length)
  const barW = Math.max(2, Math.min(24, slot - 2)) // thin marks, 2px gap
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH
  const first = data[0]
  const last = data[data.length - 1]
  const middle = data[Math.floor(data.length / 2)]

  return (
    <figure className="rounded border border-slate-200 p-4 dark:border-slate-800">
      <figcaption className="mb-2 font-medium">{title}</figcaption>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="group"
        aria-label={title}
        className="h-auto w-full"
      >
        <line
          x1={PAD.left}
          x2={WIDTH - PAD.right}
          y1={y(top)}
          y2={y(top)}
          className="stroke-slate-200 dark:stroke-slate-800"
        />
        <text
          x={PAD.left - 6}
          y={y(top) + 4}
          textAnchor="end"
          className="fill-slate-500 text-[10px] dark:fill-slate-400"
        >
          {formatAxis(top)}
        </text>
        <text
          x={PAD.left - 6}
          y={y(0) + 4}
          textAnchor="end"
          className="fill-slate-500 text-[10px] dark:fill-slate-400"
        >
          {formatAxis(0)}
        </text>
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
              rx={Math.min(4, barW / 2)}
              tabIndex={0}
              role="img"
              aria-label={`${d.label}: ${d.text}`}
              className="fill-[#2a78d6] hover:opacity-80 focus:opacity-80 dark:fill-[#3987e5]"
            >
              <title>{`${d.label}: ${d.text}`}</title>
            </rect>
          )
        })}
        <line
          x1={PAD.left}
          x2={WIDTH - PAD.right}
          y1={y(0)}
          y2={y(0)}
          className="stroke-slate-400 dark:stroke-slate-600"
        />
        {[first, middle, last].map(
          (d, i) =>
            d && (
              <text
                key={`${d.label}-${i}`}
                x={PAD.left + (i === 0 ? 0 : i === 1 ? data.indexOf(d) * slot + slot / 2 : plotW)}
                y={HEIGHT - 6}
                textAnchor={i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}
                className="fill-slate-500 text-[10px] dark:fill-slate-400"
              >
                {d.label}
              </text>
            ),
        )}
      </svg>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-slate-600 dark:text-slate-400">
          Show as table
        </summary>
        <table className="mt-2 w-full text-left">
          <thead>
            <tr>
              <th className="py-1 font-medium">Day</th>
              <th className="py-1 font-medium">Value</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.label} className="border-t border-slate-100 dark:border-slate-800">
                <td className="py-1">{d.label}</td>
                <td className="py-1">{d.value === null ? 'no data' : d.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  )
}
