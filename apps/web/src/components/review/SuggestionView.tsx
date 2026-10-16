import { Wrench } from 'lucide-react'
import { classifySuggestion } from '../../lib/suggestion'

/** A suggested fix: prose as plain text, only actual code in code blocks. Never HTML. */
export function SuggestionView({ suggestion }: { suggestion: string }) {
  const blocks = classifySuggestion(suggestion)
  if (blocks.length === 0) return null
  return (
    <div className="mt-3 space-y-2 text-sm">
      <p className="flex items-center gap-1.5 text-xs font-medium text-muted">
        <Wrench aria-hidden="true" className="size-3.5" />
        Suggested fix
      </p>
      {blocks.map((block, i) =>
        block.kind === 'code' ? (
          <pre
            key={i}
            className="overflow-x-auto rounded-md border border-line bg-surface-2 px-3 py-2 font-mono text-xs leading-5 text-fg"
          >
            <code>{block.text}</code>
          </pre>
        ) : (
          <p key={i} className="whitespace-pre-wrap text-fg">
            {block.text}
          </p>
        ),
      )}
    </div>
  )
}
