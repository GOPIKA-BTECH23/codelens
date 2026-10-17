import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Link } from 'react-router'
import { api } from '../api/client'
import { ThemeToggle } from '../components/ThemeToggle'
import { Badge } from '../components/ui/Badge'
import { Card, CardHeader } from '../components/ui/Card'
import { Logo } from '../components/ui/Logo'
import { buttonStyles } from '../components/ui/styles'

export function HealthPage() {
  const { data, error, isPending } = useQuery({ queryKey: ['health'], queryFn: api.health })

  return (
    <main className="mx-auto max-w-xl px-4 py-10">
      <div className="flex items-center justify-between">
        <Logo />
        <ThemeToggle />
      </div>
      <h1 className="mt-8 text-2xl font-semibold tracking-tight text-fg">System status</h1>
      <p className="mt-1 text-sm text-muted">AI security review for GitHub pull requests.</p>

      <section aria-labelledby="health-heading" className="mt-6">
        <Card>
          <CardHeader title="API status" titleId="health-heading" />
          <div className="p-4 text-sm">
            {isPending && (
              <p role="status" className="text-muted">
                Checking the API…
              </p>
            )}
            {error && (
              <p role="alert" className="text-danger-fg">
                API unreachable: {error.message}
              </p>
            )}
            {data && (
              <dl className="grid grid-cols-[auto_1fr] items-center gap-x-6 gap-y-2">
                <dt className="text-muted">Status</dt>
                <dd>
                  <Badge tone={data.status === 'ok' ? 'success' : 'danger'}>{data.status}</Badge>
                </dd>
                <dt className="text-muted">Version</dt>
                <dd className="font-mono text-xs">{data.version}</dd>
                <dt className="text-muted">Database</dt>
                <dd>
                  <Badge tone={data.database === 'ok' ? 'success' : 'danger'}>
                    {data.database}
                  </Badge>
                </dd>
                <dt className="text-muted">Migration</dt>
                <dd className="font-mono text-xs">{data.revision ?? 'none'}</dd>
              </dl>
            )}
          </div>
        </Card>
      </section>

      <Link to="/" className={buttonStyles({ variant: 'ghost', className: 'mt-6 -ml-3' })}>
        <ArrowLeft aria-hidden="true" />
        Back to CodeLens
      </Link>
    </main>
  )
}
