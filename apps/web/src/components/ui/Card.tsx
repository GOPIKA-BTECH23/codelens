import type { HTMLAttributes, ReactNode } from 'react'
import { cardStyles } from './styles'

export function Card({ className = '', ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`${cardStyles} ${className}`} {...rest} />
}

interface HeaderProps {
  title: ReactNode
  /** id for the heading, so a surrounding <section aria-labelledby> can point at it. */
  titleId?: string
  description?: ReactNode
  actions?: ReactNode
  /** Heading level; defaults to h2. */
  as?: 'h2' | 'h3'
}

export function CardHeader({ title, titleId, description, actions, as = 'h2' }: HeaderProps) {
  const Heading = as
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
      <div className="min-w-0">
        <Heading id={titleId} className="text-sm font-semibold text-fg">
          {title}
        </Heading>
        {description && <p className="mt-0.5 text-xs text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}
