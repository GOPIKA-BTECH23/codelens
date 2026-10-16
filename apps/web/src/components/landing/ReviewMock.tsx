import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { SeverityBadge } from '../StatusBadges'
import { Logo } from '../ui/Logo'

const LINES: { n: number; kind: ' ' | '+'; text: string }[] = [
  { n: 12, kind: ' ', text: 'def find_user(db, user_id):' },
  { n: 13, kind: '+', text: '    query = f"SELECT * FROM users WHERE id = {user_id}"' },
  { n: 14, kind: '+', text: '    return db.execute(query).fetchone()' },
]

/**
 * A static picture of what CodeLens posts, so a visitor sees the product before signing in.
 * It is an illustration (labelled as such), not real data, and nothing in it is interactive.
 */
export function ReviewMock() {
  return (
    <figure className="overflow-hidden rounded-xl border border-line bg-surface shadow-pop">
      <div className="flex items-center justify-between border-b border-line bg-surface-2 px-4 py-2.5">
        <span className="font-mono text-xs text-fg">app/users.py</span>
        <span className="text-xs text-muted">
          <span className="text-success-fg">+2</span> <span className="text-danger-fg">−0</span>
        </span>
      </div>
      <div className="overflow-x-auto font-mono text-xs leading-6">
        {LINES.map((line) => (
          <div key={line.n} className={`flex ${line.kind === '+' ? 'bg-diff-add' : ''}`}>
            <span
              className={`w-10 shrink-0 pr-2 text-right text-muted select-none ${line.kind === '+' ? 'bg-diff-add-gutter' : ''}`}
            >
              {line.n}
            </span>
            <span className="w-5 shrink-0 text-center text-muted select-none">
              {line.kind === '+' ? '+' : ''}
            </span>
            <span className="pr-4 whitespace-pre text-fg">{line.text}</span>
          </div>
        ))}
      </div>

      <div className="border-t border-line bg-canvas/60 p-3 sm:p-4">
        <div className="rounded-lg border border-line border-l-4 border-l-orange-fg bg-surface p-3.5">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Logo withText={false} />
            <span className="font-semibold text-fg">codelens</span>
            <span className="rounded border border-line px-1 text-2xs text-muted">bot</span>
            <span className="text-muted">commented on line 14</span>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <SeverityBadge severity="high" />
            <span className="text-sm font-medium text-fg">sql-injection</span>
            <span className="text-xs text-muted">confidence 0.92</span>
          </div>
          <p className="mt-2 text-sm text-fg">
            <code className="rounded bg-surface-2 px-1 text-xs">user_id</code> is formatted into the
            SQL string, so a crafted id can read or change any row.
          </p>
          <p className="mt-2 text-xs font-medium text-muted">Suggested fix</p>
          <pre className="mt-1 overflow-x-auto rounded-md bg-surface-2 p-2 text-xs text-fg">
            <code>{'db.execute("SELECT * FROM users WHERE id = ?", (user_id,))'}</code>
          </pre>
          <div aria-hidden="true" className="mt-3 flex gap-2 text-xs text-muted">
            <span className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1">
              <ThumbsUp className="size-3.5" /> Useful
            </span>
            <span className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1">
              <ThumbsDown className="size-3.5" /> Not useful
            </span>
          </div>
        </div>
      </div>
      <figcaption className="border-t border-line px-4 py-2 text-2xs text-muted">
        Example of a CodeLens review comment (illustration, not real data).
      </figcaption>
    </figure>
  )
}
