import { NavLink, Outlet, useNavigate } from 'react-router'
import { useAuth } from '../auth/context'
import { isAnyAdmin } from '../lib/roles'
import { useTheme } from '../theme/theme'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded px-3 py-1.5 text-sm ${
    isActive
      ? 'bg-slate-100 font-medium dark:bg-slate-800'
      : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'
  }`

/** Layout for signed-in pages: skip link, header with navigation, theme toggle and user. */
export function AppShell() {
  const { me, logout, loggingOut } = useAuth()
  const { theme, toggle } = useTheme()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    await navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:m-2 focus:rounded focus:bg-white focus:p-2 focus:text-slate-900"
      >
        Skip to content
      </a>
      <header className="border-b border-slate-200 dark:border-slate-800">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-4">
            <span className="font-bold">CodeLens</span>
            <nav aria-label="Main" className="flex gap-1">
              <NavLink to="/dashboard" className={navClass}>
                Dashboard
              </NavLink>
              {isAnyAdmin(me) && (
                <NavLink to="/analytics" className={navClass}>
                  Analytics
                </NavLink>
              )}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={toggle}
              aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
              className="rounded border border-slate-300 px-2 py-1 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
            >
              {theme === 'dark' ? '☀ Light' : '☾ Dark'}
            </button>
            {me?.user.avatar_url && (
              <img src={me.user.avatar_url} alt="" className="h-7 w-7 rounded-full" />
            )}
            <span className="text-sm text-slate-700 dark:text-slate-300">{me?.user.login}</span>
            <button
              type="button"
              onClick={() => void handleLogout()}
              disabled={loggingOut}
              className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
            >
              {loggingOut ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
