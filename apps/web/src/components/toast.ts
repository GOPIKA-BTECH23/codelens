import { createContext, use } from 'react'

export type ToastKind = 'error' | 'success' | 'info'

export interface ToastApi {
  show: (message: string, kind?: ToastKind) => void
}

export const ToastContext = createContext<ToastApi | null>(null)

export function useToast(): ToastApi {
  const value = use(ToastContext)
  if (!value) throw new Error('useToast must be used inside <ToastProvider>')
  return value
}
