import { type ReactNode, useCallback, useMemo, useRef, useState } from 'react'
import { ToastContext, type ToastKind } from './toast'

interface ToastItem {
  id: number
  message: string
  kind: ToastKind
}

const DISMISS_AFTER_MS = 6000

const KIND_STYLES: Record<ToastKind, string> = {
  error:
    'border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100',
  success:
    'border-green-300 bg-green-50 text-green-900 dark:border-green-800 dark:bg-green-950 dark:text-green-100',
  info: 'border-slate-300 bg-white text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100',
}

/**
 * Toasts in a live region: screen readers announce new messages without moving focus.
 * (role="status" is polite; errors use role="alert" so they interrupt.)
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((t) => t.id !== id))
  }, [])

  const show = useCallback(
    (message: string, kind: ToastKind = 'info') => {
      const id = nextId.current++
      setToasts((current) => [...current, { id, message, kind }])
      setTimeout(() => dismiss(id), DISMISS_AFTER_MS)
    },
    [dismiss],
  )

  const value = useMemo(() => ({ show }), [show])

  return (
    <ToastContext value={value}>
      {children}
      <div className="fixed right-4 bottom-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.kind === 'error' ? 'alert' : 'status'}
            className={`flex items-start justify-between gap-3 rounded border p-3 text-sm shadow ${KIND_STYLES[t.kind]}`}
          >
            <span>{t.message}</span>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss notification"
              className="shrink-0 rounded px-1 text-lg leading-none opacity-70 hover:opacity-100"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext>
  )
}
