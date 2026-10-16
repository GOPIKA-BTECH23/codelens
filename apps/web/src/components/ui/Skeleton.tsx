import { cardStyles } from './styles'

/** A grey placeholder block. Decorative: the surrounding status region carries the text. */
export function Skeleton({ className = '' }: { className?: string }) {
  return <div aria-hidden="true" className={`animate-pulse rounded-md bg-surface-2 ${className}`} />
}

/** Placeholder for a list of rows while it loads. Screen readers hear `label`. */
export function ListSkeleton({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div role="status" className={`${cardStyles} divide-y divide-line`}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center justify-between gap-4 px-4 py-3.5">
          <div className="flex-1 space-y-2">
            <Skeleton className="h-3.5 w-1/3" />
            <Skeleton className="h-3 w-1/2" />
          </div>
          <Skeleton className="h-5 w-20 rounded-full" />
        </div>
      ))}
    </div>
  )
}

/** Placeholder for a whole page (header + a few blocks). */
export function PageSkeleton({ label }: { label: string }) {
  return (
    <div role="status" className="space-y-6">
      <span className="sr-only">{label}</span>
      <div className="space-y-2">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-7 w-2/3 max-w-md" />
        <Skeleton className="h-3 w-40" />
      </div>
      <Skeleton className="h-36 w-full rounded-lg" />
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  )
}
