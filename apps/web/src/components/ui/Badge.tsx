import type { ReactNode } from 'react'
import type { Tone } from '../../lib/labels'

const TONES: Record<Tone, string> = {
  neutral: 'border-neutral-line bg-neutral-bg text-neutral-fg',
  accent: 'border-accent/25 bg-accent-soft text-accent',
  success: 'border-success-line bg-success-bg text-success-fg',
  warning: 'border-warning-line bg-warning-bg text-warning-fg',
  orange: 'border-orange-line bg-orange-bg text-orange-fg',
  danger: 'border-danger-line bg-danger-bg text-danger-fg',
  info: 'border-info-line bg-info-bg text-info-fg',
}

interface Props {
  tone: Tone
  children: ReactNode
  /** A small icon before the text. The text always carries the meaning; colour and icon
   *  only reinforce it, so nothing depends on seeing colour. */
  icon?: ReactNode
}

export function Badge({ tone, children, icon }: Props) {
  return (
    <span
      className={`inline-flex h-5 items-center gap-1 rounded-full border px-2 text-2xs font-medium whitespace-nowrap [&_svg]:size-3 [&_svg]:shrink-0 ${TONES[tone]}`}
    >
      {icon && (
        <span aria-hidden="true" className="contents">
          {icon}
        </span>
      )}
      {children}
    </span>
  )
}
