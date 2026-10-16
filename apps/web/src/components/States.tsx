import { AlertCircle, Inbox, RotateCw } from 'lucide-react'
import type { ReactNode } from 'react'
import { errorMessage } from '../lib/errors'
import { Button } from './ui/Button'

/** Small inline loading line (use the skeletons in ui/Skeleton for whole lists or pages). */
export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <p role="status" className="flex items-center gap-2 p-6 text-sm text-muted">
      <span
        aria-hidden="true"
        className="size-4 animate-spin rounded-full border-2 border-line border-t-accent-solid"
      />
      {label}
    </p>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-start gap-3 rounded-lg border border-danger-line bg-danger-bg p-4 text-danger-fg"
    >
      <AlertCircle aria-hidden="true" className="mt-0.5 size-4.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">Something went wrong</p>
        <p className="mt-0.5 text-sm">{errorMessage(error)}</p>
      </div>
      {onRetry && (
        <Button size="sm" onClick={onRetry} icon={<RotateCw />}>
          Try again
        </Button>
      )}
    </div>
  )
}

interface EmptyProps {
  title: string
  children?: ReactNode
  /** Defaults to an inbox. */
  icon?: ReactNode
  /** A call to action (a link or button styled with buttonStyles). */
  action?: ReactNode
}

export function EmptyState({ title, children, icon, action }: EmptyProps) {
  return (
    <div className="flex flex-col items-center rounded-lg border border-dashed border-line-strong bg-surface/50 px-6 py-10 text-center">
      <div
        aria-hidden="true"
        className="mb-3 flex size-10 items-center justify-center rounded-full bg-surface-2 text-muted [&_svg]:size-5"
      >
        {icon ?? <Inbox />}
      </div>
      <p className="font-medium text-fg">{title}</p>
      {children && <div className="mt-1 max-w-prose text-sm text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}
