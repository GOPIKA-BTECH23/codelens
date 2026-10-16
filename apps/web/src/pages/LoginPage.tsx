import { Navigate, useSearchParams } from 'react-router'
import { useAuth } from '../auth/context'
import { PublicHome } from './LandingPage'

// Error codes the API's OAuth callback puts in /login?error=...
const LOGIN_ERRORS: Record<string, string> = {
  access_denied: 'GitHub sign-in was cancelled.',
  invalid_state: 'That sign-in link expired or was not started here. Please try again.',
  github_error: 'GitHub could not complete the sign-in. Please try again.',
}

/** /login is the public page plus, after a failed sign-in, an explanation. */
export function LoginPage() {
  const auth = useAuth()
  const [params] = useSearchParams()
  const errorCode = params.get('error')

  if (auth.status === 'authenticated') return <Navigate to="/dashboard" replace />
  const error = errorCode ? (LOGIN_ERRORS[errorCode] ?? 'Sign-in failed. Please try again.') : null
  return <PublicHome error={error} />
}
