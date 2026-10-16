import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { type ButtonSize, type ButtonVariant, buttonStyles } from './styles'

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  /** An icon element rendered before the label (decorative: hidden from screen readers). */
  icon?: ReactNode
}

/** The one button. `type="button"` by default so it never submits a form by accident. */
export function Button({
  variant,
  size,
  icon,
  className,
  children,
  type = 'button',
  ...rest
}: Props) {
  return (
    <button type={type} className={buttonStyles({ variant, size, className })} {...rest}>
      {icon && (
        <span aria-hidden="true" className="contents">
          {icon}
        </span>
      )}
      {children}
    </button>
  )
}
