import { CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { type ReactNode, useCallback, useMemo, useRef, useState } from 'react'
import { ToastContext, type ToastKind } from './toast'

interface ToastItem {
  id: number
  message: string
  kind: ToastKind
}

const DISMISS_AFTER_MS = 6000

const KIND: Record<ToastKind, { icon: ReactNode; className: string }> = {
  error: { icon: <XCircle />, className: 'text-danger-fg' },
  success: { icon: <CheckCircle2 />, className: 'text-success-fg' },
  info: { icon: <Info />, className: 'text-info-fg' },
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
      <div className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-88 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.kind === 'error' ? 'alert' : 'status'}
            className="pointer-events-auto flex items-start gap-2.5 rounded-lg border border-line bg-surface p-3 text-sm text-fg shadow-pop motion-safe:animate-[toast-in_160ms_ease-out]"
          >
            <span aria-hidden="true" className={`mt-0.5 [&_svg]:size-4 ${KIND[t.kind].className}`}>
              {KIND[t.kind].icon}
            </span>
            <span className="min-w-0 flex-1">{t.message}</span>
            <button
              type="button"
              onClick={() => dismiss(t.id)}
              aria-label="Dismiss notification"
              className="-m-1 rounded-md p-1 text-muted hover:bg-surface-2 hover:text-fg"
            >
              <X aria-hidden="true" className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext>
  )
}
