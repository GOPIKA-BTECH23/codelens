import { ChartColumn, LayoutDashboard } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router'
import { useAuth } from '../auth/context'
import { isAnyAdmin } from '../lib/roles'
import { ThemeToggle } from './ThemeToggle'
import { Logo } from './ui/Logo'
import { UserMenu } from './UserMenu'

function NavItem({ to, icon, children }: { to: string; icon: ReactNode; children: ReactNode }) {
  // NavLink sets aria-current="page" on the active link; the style follows that attribute.
  return (
    <NavLink
      to={to}
      className="relative flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm text-muted transition-colors duration-150 hover:bg-surface-2 hover:text-fg aria-[current=page]:bg-surface-2 aria-[current=page]:font-medium aria-[current=page]:text-fg [&_svg]:size-4"
    >
      <span aria-hidden="true" className="contents">
        {icon}
      </span>
      {children}
    </NavLink>
  )
}

/** Layout for signed-in pages: skip link, sticky top bar, centred content column. */
export function AppShell() {
  const { me, logout, loggingOut } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    await navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only z-50 focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:shadow-pop"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-30 border-b border-line bg-canvas/85 backdrop-blur supports-[backdrop-filter]:bg-canvas/70">
        <div className="mx-auto flex h-14 max-w-page items-center justify-between gap-3 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-4 sm:gap-6">
            <Link to="/dashboard" aria-label="CodeLens home" className="rounded-md">
              <Logo />
            </Link>
            <nav aria-label="Main" className="flex items-center gap-1">
              <NavItem to="/dashboard" icon={<LayoutDashboard />}>
                Dashboard
              </NavItem>
              {isAnyAdmin(me) && (
                <NavItem to="/analytics" icon={<ChartColumn />}>
                  Analytics
                </NavItem>
              )}
            </nav>
          </div>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            {me && (
              <UserMenu me={me} onSignOut={() => void handleLogout()} signingOut={loggingOut} />
            )}
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-page px-4 py-8 sm:px-6">
        <Outlet />
      </main>
    </div>
  )
}
