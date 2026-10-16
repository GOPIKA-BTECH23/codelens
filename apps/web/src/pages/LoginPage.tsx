import { Navigate, useSearchParams } from 'react-router'
import { GITHUB_LOGIN_URL } from '../api/client'
import { useAuth } from '../auth/context'

// Error codes the API's OAuth callback puts in /login?error=...
const LOGIN_ERRORS: Record<string, string> = {
  access_denied: 'GitHub sign-in was cancelled.',
  invalid_state: 'That sign-in link expired or was not started here. Please try again.',
  github_error: 'GitHub could not complete the sign-in. Please try again.',
}

export function LoginPage() {
  const auth = useAuth()
  const [params] = useSearchParams()
  const errorCode = params.get('error')

  if (auth.status === 'authenticated') return <Navigate to="/dashboard" replace />

  return (
    <main className="mx-auto max-w-md p-8">
      <h1 className="text-3xl font-bold">CodeLens</h1>
      <p className="mt-1 text-gray-600">AI code review and repo chat.</p>

      {errorCode && (
        <p role="alert" className="mt-6 rounded border border-red-200 bg-red-50 p-3 text-red-800">
          {LOGIN_ERRORS[errorCode] ?? 'Sign-in failed. Please try again.'}
        </p>
      )}

      {/* A real link, not fetch(): the browser must navigate to GitHub and back so the
          OAuth redirect and its cookies happen at the top level. */}
      <a
        href={GITHUB_LOGIN_URL}
        className="mt-8 inline-block rounded bg-gray-900 px-4 py-2 font-medium text-white hover:bg-gray-700"
      >
        Sign in with GitHub
      </a>
    </main>
  )
}
