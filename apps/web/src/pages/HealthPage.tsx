import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export function HealthPage() {
  const { data, error, isPending } = useQuery({ queryKey: ['health'], queryFn: api.health })

  return (
    <main className="mx-auto max-w-xl p-8">
      <h1 className="text-3xl font-bold">CodeLens</h1>
      <p className="mt-1 text-gray-600">AI code review and repo chat.</p>

      <section aria-labelledby="health-heading" className="mt-8 rounded-lg border p-4">
        <h2 id="health-heading" className="font-semibold">
          API status
        </h2>
        {isPending && <p role="status">Checking the API…</p>}
        {error && (
          <p role="alert" className="text-red-700">
            API unreachable: {error.message}
          </p>
        )}
        {data && (
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
            <dt className="text-gray-600">Status</dt>
            <dd>{data.status}</dd>
            <dt className="text-gray-600">Version</dt>
            <dd>{data.version}</dd>
            <dt className="text-gray-600">Database</dt>
            <dd>{data.database}</dd>
            <dt className="text-gray-600">Migration</dt>
            <dd>{data.revision ?? 'none'}</dd>
          </dl>
        )}
      </section>
    </main>
  )
}
