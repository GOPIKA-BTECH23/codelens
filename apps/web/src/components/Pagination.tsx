import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from './ui/Button'

interface Props {
  page: number // zero-based
  pageSize: number
  total: number
  onPage: (page: number) => void
}

export function Pagination({ page, pageSize, total, onPage }: Props) {
  if (total <= pageSize) return null
  const pages = Math.ceil(total / pageSize)
  const first = page * pageSize + 1
  const last = Math.min(total, (page + 1) * pageSize)
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between gap-3 px-1 pt-3">
      <p className="text-xs text-muted tabular-nums">
        {first}–{last} of {total}
      </p>
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
          icon={<ChevronLeft />}
        >
          Previous
        </Button>
        <Button size="sm" disabled={page >= pages - 1} onClick={() => onPage(page + 1)}>
          Next
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </nav>
  )
}
