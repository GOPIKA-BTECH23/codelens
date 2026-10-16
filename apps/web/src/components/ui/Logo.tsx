/** The CodeLens mark (a lens over a code bracket) and wordmark. */
export function Logo({ withText = true }: { withText?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2 font-semibold tracking-tight text-fg">
      <svg viewBox="0 0 24 24" aria-hidden="true" className="size-6 shrink-0">
        <rect width="24" height="24" rx="6" className="fill-accent-solid" />
        <path
          d="M9.5 8.5 6.5 12l3 3.5M14.5 8.5l3 3.5-3 3.5"
          fill="none"
          stroke="white"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {withText && <span>CodeLens</span>}
    </span>
  )
}
