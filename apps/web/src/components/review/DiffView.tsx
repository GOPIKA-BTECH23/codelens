import { ChevronDown, FileCode2, MessageSquareWarning } from 'lucide-react'
import { useState } from 'react'
import type { DiffLineOut, FindingOut, FindingUpdate, ReviewFileOut } from '../../api/types'
import { anchorLine, newLinesOf } from '../../lib/diffAnchor'
import { Badge } from '../ui/Badge'
import { FindingCard } from './FindingCard'

const ROW = {
  '+': { row: 'bg-diff-add', gutter: 'bg-diff-add-gutter', marker: 'text-success-fg' },
  '-': { row: 'bg-diff-del', gutter: 'bg-diff-del-gutter', marker: 'text-danger-fg' },
  ' ': { row: '', gutter: '', marker: '' },
} as const

interface Props {
  file: ReviewFileOut
  findings: FindingOut[] // already filtered, only this file's
  onUpdate: (findingId: string, change: FindingUpdate) => void
}

/** One file's diff as a table, with finding cards inserted under the line they point at. */
export function DiffView({ file, findings, onUpdate }: Props) {
  const [open, setOpen] = useState(true)
  const shown = newLinesOf(file)
  const byLine = new Map<number, FindingOut[]>()
  for (const finding of findings) {
    const at = anchorLine(finding, shown)
    if (at !== null) byLine.set(at, [...(byLine.get(at) ?? []), finding])
  }
  const bodyId = `diff-${file.path}`

  return (
    <section
      aria-label={`Changes in ${file.path}`}
      className="overflow-hidden rounded-lg border border-line bg-surface shadow-card"
    >
      <h3 className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-surface-2 px-3 py-2">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((v) => !v)}
          className="flex min-w-0 items-center gap-2 rounded-md text-left text-sm"
        >
          <ChevronDown
            aria-hidden="true"
            className={`size-4 shrink-0 text-muted transition-transform duration-150 ${open ? '' : '-rotate-90'}`}
          />
          <FileCode2 aria-hidden="true" className="size-4 shrink-0 text-muted" />
          <span className="font-mono text-xs font-medium break-all text-fg">
            {file.old_path ? `${file.old_path} → ` : ''}
            {file.path}
          </span>
        </button>
        <span className="flex items-center gap-2 text-xs text-muted">
          {findings.length > 0 && (
            <Badge tone="warning" icon={<MessageSquareWarning />}>
              {findings.length} {findings.length === 1 ? 'finding' : 'findings'}
            </Badge>
          )}
          <span>{file.status}</span>
          <span className="font-mono tabular-nums">
            <span className="text-success-fg">+{file.additions}</span>{' '}
            <span className="text-danger-fg">−{file.deletions}</span>
          </span>
        </span>
      </h3>

      <div id={bodyId} hidden={!open}>
        {!file.patch_available ? (
          <p className="p-4 text-sm text-muted">
            The diff for this file is not available (it was not stored, or has been purged).
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse font-mono text-xs leading-5">
              <caption className="sr-only">Diff of {file.path}</caption>
              {file.hunks.map((hunk) => (
                <tbody key={hunk.header}>
                  <tr className="bg-diff-hunk text-muted">
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
      </div>
    </section>
  )
}

function DiffRow({
  line,
  cards,
  onUpdate,
}: {
  line: DiffLineOut
  cards: FindingOut[] | undefined
  onUpdate: (findingId: string, change: FindingUpdate) => void
}) {
  const style = ROW[line.kind]
  const hasCards = !!cards?.length
  // Line numbers use the `muted` token: it keeps AA contrast on the tinted gutters too.
  const gutter = `w-12 min-w-12 px-2 text-right align-top text-muted tabular-nums select-none ${style.gutter}`
  return (
    <>
      <tr
        className={`${style.row} ${hasCards ? 'shadow-[inset_3px_0_0_var(--color-accent-solid)]' : ''}`}
      >
        <td className={gutter}>{line.old_line ?? ''}</td>
        <td className={gutter}>{line.new_line ?? ''}</td>
        <td className={`w-5 text-center align-top select-none ${style.marker}`} aria-hidden="true">
          {line.kind === ' ' ? '' : line.kind}
        </td>
        <td className="pr-4 whitespace-pre text-fg">
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
          <td
            colSpan={4}
            className="border-y border-line bg-canvas px-3 py-3 whitespace-normal sm:pl-[7.25rem]"
          >
            <FindingCard finding={finding} onUpdate={(change) => onUpdate(finding.id, change)} />
          </td>
        </tr>
      ))}
    </>
  )
}
