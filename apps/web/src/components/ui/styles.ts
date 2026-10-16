// Class recipes shared by the ui components AND by elements that must stay a different tag
// (a <Link> or <a> that looks like a button). Kept out of the .tsx files so React Fast Refresh
// works (a component file should only export components).

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'sm' | 'md' | 'lg'

const BUTTON_BASE =
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium ' +
  'transition-colors duration-150 select-none ' +
  'disabled:pointer-events-none disabled:opacity-50 [&_svg]:shrink-0'

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: 'bg-accent-solid text-on-accent shadow-card hover:bg-accent-solid-hover',
  secondary:
    'border border-line bg-surface text-fg shadow-card hover:border-line-strong hover:bg-surface-2',
  ghost: 'text-muted hover:bg-surface-2 hover:text-fg',
  danger: 'border border-danger-line bg-danger-bg text-danger-fg hover:border-danger-fg/40',
}

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-xs [&_svg]:size-3.5',
  md: 'h-8 px-3 text-sm [&_svg]:size-4',
  lg: 'h-10 px-4 text-base [&_svg]:size-4.5',
}

export function buttonStyles({
  variant = 'secondary',
  size = 'md',
  className = '',
}: { variant?: ButtonVariant; size?: ButtonSize; className?: string } = {}): string {
  return `${BUTTON_BASE} ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`.trim()
}

export const controlStyles =
  'h-8 rounded-md border border-line bg-surface px-2.5 text-sm text-fg shadow-card ' +
  'placeholder:text-subtle transition-colors duration-150 hover:border-line-strong ' +
  'focus-visible:border-accent-solid'

export const cardStyles = 'rounded-lg border border-line bg-surface shadow-card'

export const linkStyles =
  'font-medium text-accent underline-offset-2 hover:underline focus-visible:underline'
