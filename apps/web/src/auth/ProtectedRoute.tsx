import { RotateCw } from 'lucide-react'
import { Navigate, Outlet } from 'react-router'
import { Loading } from '../components/States'
import { Button } from '../components/ui/Button'
import { Logo } from '../components/ui/Logo'
import { useAuth } from './context'

/**
 * Renders the nested routes only for a signed-in user; otherwise sends them to /login.
 *
 * This is a convenience for the UI, NOT security: anyone can edit JavaScript in their own
 * browser. The real check is on the API, which answers 401/403/404 for every request.
 */
export function ProtectedRoute() {
  const auth = useAuth()

  if (auth.status === 'loading') {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loading label="Loading your session…" />
      </div>
    )
  }
  if (auth.status === 'error') {
    return (
      <div className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 px-4">
        <Logo />
        <div
          role="alert"
          className="rounded-lg border border-danger-line bg-danger-bg p-4 text-sm text-danger-fg"
        >
          <p className="font-medium">Could not reach the CodeLens API</p>
          <p className="mt-0.5">{auth.error?.message}</p>
        </div>
        <div>
          <Button onClick={auth.retry} icon={<RotateCw />}>
            Try again
          </Button>
        </div>
      </div>
    )
  }
  if (auth.status === 'anonymous') return <Navigate to="/login" replace />
  return <Outlet />
}
