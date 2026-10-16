import { Navigate, Outlet } from 'react-router'
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
      <p role="status" className="p-8 text-gray-600">
        Loading your session…
      </p>
    )
  }
  if (auth.status === 'error') {
    return (
      <div role="alert" className="mx-auto max-w-xl p-8">
        <p className="text-red-700">Could not reach the API: {auth.error?.message}</p>
        <button type="button" onClick={auth.retry} className="mt-4 rounded border px-3 py-1">
          Try again
        </button>
      </div>
    )
  }
  if (auth.status === 'anonymous') return <Navigate to="/login" replace />
  return <Outlet />
}
