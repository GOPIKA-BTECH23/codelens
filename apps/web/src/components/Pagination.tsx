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
  const button =
    'rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-40 dark:border-slate-700 dark:hover:bg-slate-800'
  return (
    <nav aria-label="Pagination" className="mt-4 flex items-center justify-between gap-3">
      <p className="text-sm text-slate-600 dark:text-slate-400">
        {first}–{last} of {total}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className={button}
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </button>
        <button
          type="button"
          className={button}
          disabled={page >= pages - 1}
          onClick={() => onPage(page + 1)}
        >
          Next
        </button>
      </div>
    </nav>
  )
}
