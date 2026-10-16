import { classifySuggestion } from '../../lib/suggestion'

/** A suggested fix: prose as plain text, only actual code in code blocks. */
export function SuggestionView({ suggestion }: { suggestion: string }) {
  const blocks = classifySuggestion(suggestion)
  if (blocks.length === 0) return null
  return (
    <div className="mt-2 space-y-2 text-sm">
      <p className="font-medium text-slate-700 dark:text-slate-300">Suggested fix</p>
      {blocks.map((block, i) =>
        block.kind === 'code' ? (
          <pre
            key={i}
            className="overflow-x-auto rounded bg-slate-100 p-2 font-mono text-xs dark:bg-slate-900"
          >
            <code>{block.text}</code>
          </pre>
        ) : (
          <p key={i} className="whitespace-pre-wrap">
            {block.text}
          </p>
        ),
      )}
    </div>
  )
}
