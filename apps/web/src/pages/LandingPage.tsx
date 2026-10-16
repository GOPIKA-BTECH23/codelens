import { Link, Navigate } from 'react-router'
import { GITHUB_LOGIN_URL } from '../api/client'
import { useAuth } from '../auth/context'

const FEATURES = [
  {
    title: 'Automated PR review',
    text: 'A GitHub App reviews every pull request for security problems and posts inline comments on the exact lines.',
  },
  {
    title: 'Findings you can rate',
    text: 'Mark each finding useful or not useful. The feedback shows how often the reviewer is right.',
  },
  {
    title: 'Honest numbers',
    text: 'Model, prompt version, latency and estimated cost are shown for every review run.',
  },
]

export function LandingPage() {
  const auth = useAuth()
  if (auth.status === 'authenticated') return <Navigate to="/dashboard" replace />

  return (
    <main className="mx-auto max-w-3xl px-4 py-16">
      <h1 className="text-4xl font-bold">CodeLens</h1>
      <p className="mt-3 text-lg text-slate-600 dark:text-slate-400">
        AI code review for your pull requests, and a dashboard to see what it found.
      </p>
      <div className="mt-8 flex flex-wrap gap-3">
        {/* A real link, not fetch(): the browser must navigate to GitHub and back. */}
        <a
          href={GITHUB_LOGIN_URL}
          className="rounded bg-slate-900 px-4 py-2 font-medium text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-slate-300"
        >
          Sign in with GitHub
        </a>
        <Link
          to="/health"
          className="rounded border border-slate-300 px-4 py-2 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
        >
          System status
        </Link>
      </div>
      <ul className="mt-12 grid gap-6 sm:grid-cols-3">
        {FEATURES.map((feature) => (
          <li key={feature.title}>
            <h2 className="font-semibold">{feature.title}</h2>
            <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{feature.text}</p>
          </li>
        ))}
      </ul>
      <p className="mt-12 text-sm text-slate-500 dark:text-slate-400">
        The hosted demo is meant for public or demo repositories only.
      </p>
    </main>
  )
}
