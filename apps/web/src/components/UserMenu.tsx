import { ChevronDown, LogOut } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import type { MeResponse } from '../api/types'

interface Props {
  me: MeResponse
  onSignOut: () => void
  signingOut: boolean
}

/**
 * Avatar button that opens a small panel with the account and "Sign out".
 *
 * This is the WAI-ARIA *disclosure* pattern (a button with aria-expanded controlling a region),
 * not role="menu": it holds ordinary focusable controls, so Tab works as usual. Escape and a
 * click outside close it, and Escape returns focus to the button.
 */
export function UserMenu({ me, onSignOut, signingOut }: Props) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const root = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        trigger.current?.focus()
      }
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const { user } = me
  return (
    <div ref={root} className="relative">
      <button
        ref={trigger}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={`Account menu for ${user.login}`}
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 items-center gap-2 rounded-md px-1.5 text-sm text-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg"
      >
        {user.avatar_url ? (
          <img src={user.avatar_url} alt="" className="size-6 rounded-full border border-line" />
        ) : (
          <span
            aria-hidden="true"
            className="flex size-6 items-center justify-center rounded-full bg-accent-soft text-2xs font-semibold text-accent uppercase"
          >
            {user.login.slice(0, 1)}
          </span>
        )}
        <span className="hidden max-w-36 truncate sm:inline">{user.login}</span>
        <ChevronDown aria-hidden="true" className="size-3.5" />
      </button>

      <div
        id={panelId}
        hidden={!open}
        className="absolute right-0 z-40 mt-2 w-60 rounded-lg border border-line bg-surface p-1 shadow-pop"
      >
        <div className="border-b border-line px-3 py-2.5">
          <p className="truncate text-sm font-medium text-fg">{user.name ?? user.login}</p>
          <p className="truncate text-xs text-muted">@{user.login}</p>
        </div>
        <button
          type="button"
          onClick={onSignOut}
          disabled={signingOut}
          className="mt-1 flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-fg transition-colors duration-150 hover:bg-surface-2 disabled:opacity-50"
        >
          <LogOut aria-hidden="true" className="size-4 text-muted" />
          {signingOut ? 'Signing out…' : 'Sign out'}
        </button>
      </div>
    </div>
  )
}
