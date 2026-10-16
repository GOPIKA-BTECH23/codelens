import type { FindingOut, FindingUpdate, ReviewFileOut } from '../../api/types'
import { anchorLine, newLinesOf } from '../../lib/diffAnchor'
import { FindingCard } from './FindingCard'

const ROW_STYLE = {
  '+': 'bg-green-50 dark:bg-green-950/40',
  '-': 'bg-red-50 dark:bg-red-950/40',
  ' ': '',
} as const

interface Props {
  file: ReviewFileOut
  findings: FindingOut[] // already filtered, only this file's
  onUpdate: (findingId: string, change: FindingUpdate) => void
}

/** One file's diff as a table, with finding cards inserted under the line they point at. */
export function DiffView({ file, findings, onUpdate }: Props) {
  const shown = newLinesOf(file)
  const byLine = new Map<number, FindingOut[]>()
  for (const finding of findings) {
    const at = anchorLine(finding, shown)
    if (at !== null) byLine.set(at, [...(byLine.get(at) ?? []), finding])
  }

  return (
    <section
      aria-label={`Changes in ${file.path}`}
      className="overflow-hidden rounded border border-slate-200 dark:border-slate-800"
    >
      <h3 className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2 text-sm dark:border-slate-800 dark:bg-slate-900">
        <span className="font-mono font-medium break-all">
          {file.old_path ? `${file.old_path} → ` : ''}
          {file.path}
        </span>
        <span className="text-xs text-slate-600 dark:text-slate-400">
          {file.status} ·{' '}
          <span className="text-green-700 dark:text-green-400">+{file.additions}</span>{' '}
          <span className="text-red-700 dark:text-red-400">−{file.deletions}</span>
        </span>
      </h3>

      {!file.patch_available ? (
        <p className="p-3 text-sm text-slate-600 dark:text-slate-400">
          The diff for this file is not available (it was not stored, or has been purged).
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-mono text-xs">
            <caption className="sr-only">Diff of {file.path}</caption>
            {file.hunks.map((hunk) => (
              <tbody key={hunk.header}>
                <tr className="bg-blue-50 text-slate-600 dark:bg-slate-900 dark:text-slate-400">
                  <td colSpan={4} className="px-3 py-1">
                    {hunk.header}
                  </td>
                </tr>
                {hunk.lines.map((line, i) => (
                  <DiffRow
                    key={`${i}-${line.old_line}-${line.new_line}`}
                    line={line}
                    cards={line.new_line !== null ? byLine.get(line.new_line) : undefined}
                    onUpdate={onUpdate}
                  />
                ))}
              </tbody>
            ))}
          </table>
        </div>
      )}
    </section>
  )
}

function DiffRow({
  line,
  cards,
  onUpdate,
}: {
  line: ReviewFileOut['hunks'][number]['lines'][number]
  cards: FindingOut[] | undefined
  onUpdate: (findingId: string, change: FindingUpdate) => void
}) {
  const gutter = 'w-12 min-w-12 select-none px-2 text-right text-slate-400 dark:text-slate-500'
  return (
    <>
      <tr className={ROW_STYLE[line.kind]}>
        <td className={gutter}>{line.old_line ?? ''}</td>
        <td className={gutter}>{line.new_line ?? ''}</td>
        <td className="w-4 select-none text-center" aria-hidden="true">
          {line.kind === ' ' ? '' : line.kind}
        </td>
        <td className="pr-3 whitespace-pre">
          {/* An empty line still needs a character so the row keeps its height. */}
          {line.text || ' '}
          {line.kind !== ' ' && (
            <span className="sr-only">
              {line.kind === '+' ? ' (added line)' : ' (removed line)'}
            </span>
          )}
        </td>
      </tr>
      {cards?.map((finding) => (
        <tr key={finding.id}>
          <td colSpan={4} className="bg-slate-50 px-3 py-3 whitespace-normal dark:bg-slate-950">
            <FindingCard finding={finding} onUpdate={(change) => onUpdate(finding.id, change)} />
          </td>
        </tr>
      ))}
    </>
  )
}
