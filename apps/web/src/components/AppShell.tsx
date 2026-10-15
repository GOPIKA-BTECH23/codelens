import { Outlet, useNavigate } from 'react-router'
import { useAuth } from '../auth/context'

/** Layout for signed-in pages: a header with the user and a logout button. */
export function AppShell() {
  const { me, logout, loggingOut } = useAuth()
  const navigate = useNavigate()

  async function handleLogout() {
    await logout()
    await navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between border-b px-6 py-3">
        <span className="font-bold">CodeLens</span>
        <div className="flex items-center gap-3">
          {me?.user.avatar_url && (
            <img src={me.user.avatar_url} alt="" className="h-7 w-7 rounded-full" />
          )}
          <span className="text-sm text-gray-700">{me?.user.login}</span>
          <button
            type="button"
            onClick={() => void handleLogout()}
            disabled={loggingOut}
            className="rounded border px-3 py-1 text-sm hover:bg-gray-50 disabled:opacity-50"
          >
            {loggingOut ? 'Signing out…' : 'Sign out'}
          </button>
        </div>
      </header>
      <Outlet />
    </div>
  )
}
