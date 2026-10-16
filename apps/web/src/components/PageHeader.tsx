import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

export interface Crumb {
  label: string
  to?: string
}

interface Props {
  title: ReactNode
  crumbs?: Crumb[]
  /** Badges next to the title. */
  badges?: ReactNode
  /** One line under the title. */
  meta?: ReactNode
  /** Buttons on the right. */
  actions?: ReactNode
}

/** Breadcrumbs, the page's h1, its badges, a meta line and actions: the same on every page. */
export function PageHeader({ title, crumbs, badges, meta, actions }: Props) {
  return (
    <div className="mb-8">
      {crumbs && crumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="mb-2">
          <ol className="flex flex-wrap items-center gap-1 text-xs text-muted">
            {crumbs.map((crumb, i) => (
              <li key={`${crumb.label}-${i}`} className="flex items-center gap-1">
                {i > 0 && <ChevronRight aria-hidden="true" className="size-3" />}
                {crumb.to ? (
                  <Link to={crumb.to} className="hover:text-fg hover:underline">
                    {crumb.label}
                  </Link>
                ) : (
                  <span aria-current="page" className="text-fg">
                    {crumb.label}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <h1 className="text-2xl font-semibold tracking-tight break-words text-fg">{title}</h1>
            {badges && <div className="flex flex-wrap items-center gap-1.5">{badges}</div>}
          </div>
          {meta && <div className="mt-1.5 text-sm text-muted">{meta}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  )
}
