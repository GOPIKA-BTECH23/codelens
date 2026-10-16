import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../theme/ThemeProvider'
import { ThemeToggle } from './ThemeToggle'
import { UserMenu } from './UserMenu'

const ME = {
  user: { id: 'u1', login: 'alice', name: 'Alice Anand', avatar_url: null },
  installations: [],
}

describe('user menu', () => {
  it('opens, closes with Escape and returns focus to its button', async () => {
    const onSignOut = vi.fn()
    render(<UserMenu me={ME} onSignOut={onSignOut} signingOut={false} />)
    const trigger = screen.getByRole('button', { name: 'Account menu for alice' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument() // hidden

    await userEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Alice Anand')).toBeInTheDocument()

    await userEvent.keyboard('{Escape}')
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveFocus()
  })

  it('signs out from the menu', async () => {
    const onSignOut = vi.fn()
    render(<UserMenu me={ME} onSignOut={onSignOut} signingOut={false} />)
    await userEvent.click(screen.getByRole('button', { name: 'Account menu for alice' }))
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(onSignOut).toHaveBeenCalledOnce()
  })

  it('closes when clicking outside', async () => {
    render(
      <div>
        <p>outside</p>
        <UserMenu me={ME} onSignOut={vi.fn()} signingOut={false} />
      </div>,
    )
    const trigger = screen.getByRole('button', { name: 'Account menu for alice' })
    await userEvent.click(trigger)
    await userEvent.click(screen.getByText('outside'))
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })
})

describe('theme toggle', () => {
  it('switches the dark class on <html> and says what it will do next', async () => {
    document.documentElement.classList.remove('dark')
    localStorage.setItem('codelens-theme', 'light')
    render(
      <ThemeProvider>
        <ThemeToggle />
      </ThemeProvider>,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }))

    expect(document.documentElement).toHaveClass('dark')
    expect(screen.getByRole('button', { name: 'Switch to light theme' })).toBeInTheDocument()
    expect(localStorage.getItem('codelens-theme')).toBe('dark')
  })
})
