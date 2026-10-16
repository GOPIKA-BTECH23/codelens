import type { MemberRole } from '../api/types'
import { useAuth } from '../auth/context'

const ROLE_LABEL: Record<MemberRole, string> = { owner: 'Owner', admin: 'Admin', member: 'Member' }

/** M2 placeholder dashboard: proves the session works. M6 builds the real one. */
export function DashboardPage() {
  const { me } = useAuth()
  if (!me) return null // ProtectedRoute only renders this when signed in

  return (
    <main className="mx-auto max-w-2xl p-8">
      <h1 className="text-2xl font-bold">Welcome, {me.user.name ?? me.user.login}</h1>

      <section aria-labelledby="installations-heading" className="mt-8">
        <h2 id="installations-heading" className="font-semibold">
          Your installations
        </h2>
        {me.installations.length === 0 ? (
          <p className="mt-2 text-gray-600">
            No installations yet. Install the CodeLens GitHub App on an account or organisation,
            then sign in again.
          </p>
        ) : (
          <ul className="mt-2 divide-y rounded border">
            {me.installations.map((inst) => (
              <li key={inst.id} className="flex items-center justify-between px-4 py-2">
                <span>
                  {inst.account_login}{' '}
                  <span className="text-sm text-gray-500">({inst.account_type})</span>
                </span>
                <span className="rounded bg-gray-100 px-2 py-0.5 text-sm">
                  {ROLE_LABEL[inst.role]}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  )
}
