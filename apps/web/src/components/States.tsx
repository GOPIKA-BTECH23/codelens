import type { ReactNode } from 'react'
import { errorMessage } from '../lib/errors'

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p role="status" className="p-6 text-slate-600 dark:text-slate-400">
      {label}
    </p>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="rounded border border-red-200 bg-red-50 p-4 text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100"
    >
      <p>{errorMessage(error)}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded border border-red-300 px-3 py-1 text-sm hover:bg-red-100 dark:border-red-800 dark:hover:bg-red-900"
        >
          Try again
        </button>
      )}
    </div>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded border border-dashed border-slate-300 p-8 text-center dark:border-slate-700">
      <p className="font-medium">{title}</p>
      {children && (
        <div className="mt-1 text-sm text-slate-600 dark:text-slate-400">{children}</div>
      )}
    </div>
  )
}
