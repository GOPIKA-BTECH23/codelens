import {
  Activity,
  ArrowRight,
  GitPullRequest,
  LogIn,
  MessageSquare,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Link, Navigate } from 'react-router'
import { GITHUB_LOGIN_URL } from '../api/client'
import { useAuth } from '../auth/context'
import { ReviewMock } from '../components/landing/ReviewMock'
import { ThemeToggle } from '../components/ThemeToggle'
import { Logo } from '../components/ui/Logo'
import { buttonStyles } from '../components/ui/styles'

const STEPS: { icon: ReactNode; title: string; text: string }[] = [
  {
    icon: <GitPullRequest />,
    title: 'Open a pull request',
    text: 'Install the CodeLens GitHub App on a repository and open or update a pull request as usual.',
  },
  {
    icon: <ShieldCheck />,
    title: 'CodeLens reviews it',
    text: 'An AI agent reads the diff for security problems. Every finding is checked against the changed lines; anything it cannot point at is dropped.',
  },
  {
    icon: <MessageSquare />,
    title: 'Comments appear on GitHub',
    text: 'One review with inline comments and a summary lands on the PR. Rate each finding here as useful or not.',
  },
]

/**
 * The public page: what CodeLens is, how it works, a picture of its output, and sign-in.
 * Shown at "/" and at "/login" (which adds an error banner after a failed sign-in).
 */
export function PublicHome({ error }: { error?: string | null }) {
  return (
    <div className="min-h-screen">
      <header className="mx-auto flex h-16 max-w-page items-center justify-between px-4 sm:px-6">
        <Logo />
        <div className="flex items-center gap-1">
          <Link to="/health" className={buttonStyles({ variant: 'ghost', size: 'sm' })}>
            <Activity aria-hidden="true" />
            <span className="hidden sm:inline">System status</span>
            <span className="sr-only sm:hidden">System status</span>
          </Link>
          <ThemeToggle />
          <a href={GITHUB_LOGIN_URL} className={buttonStyles({ size: 'sm', className: 'ml-1' })}>
            Sign in
          </a>
        </div>
      </header>

      <main>
        <section className="mx-auto grid max-w-page items-center gap-12 px-4 pt-10 pb-16 sm:px-6 lg:grid-cols-[1fr_1.05fr] lg:pt-16">
          <div>
            {error && (
              <p
                role="alert"
                className="mb-6 flex items-start gap-2 rounded-lg border border-danger-line bg-danger-bg p-3 text-sm text-danger-fg"
              >
                <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                {error}
              </p>
            )}
            <p className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-xs text-muted">
              <ShieldCheck aria-hidden="true" className="size-3.5 text-accent" />
              Security review for GitHub pull requests
            </p>
            <h1 className="mt-5 text-4xl leading-tight font-semibold tracking-tight text-balance text-fg sm:text-display">
              AI code review for your pull requests
            </h1>
            <p className="mt-4 max-w-prose text-lg text-muted">
              CodeLens is a GitHub App that reads every pull request, flags security problems on the
              exact lines that changed, and posts them as review comments.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              {/* A real link, not fetch(): the browser must navigate to GitHub and back so the
                  OAuth redirect and its cookies happen at the top level. */}
              <a
                href={GITHUB_LOGIN_URL}
                className={buttonStyles({ variant: 'primary', size: 'lg' })}
              >
                <LogIn aria-hidden="true" />
                Sign in with GitHub
              </a>
              <a href="#how-it-works" className={buttonStyles({ variant: 'ghost', size: 'lg' })}>
                How it works
                <ArrowRight aria-hidden="true" />
              </a>
            </div>
            <p className="mt-6 text-xs text-muted">
              A portfolio demo: please use it with public or demo repositories only.
            </p>
          </div>
          <ReviewMock />
        </section>

        <section
          id="how-it-works"
          aria-labelledby="how-heading"
          className="border-t border-line bg-surface/60"
        >
          <div className="mx-auto max-w-page px-4 py-16 sm:px-6">
            <h2 id="how-heading" className="text-2xl font-semibold tracking-tight text-fg">
              How it works
            </h2>
            <ol className="mt-8 grid gap-4 md:grid-cols-3">
              {STEPS.map((step, i) => (
                <li
                  key={step.title}
                  className="rounded-lg border border-line bg-surface p-5 shadow-card"
                >
                  <div className="flex items-center gap-3">
                    <span
                      aria-hidden="true"
                      className="flex size-9 items-center justify-center rounded-md bg-accent-soft text-accent [&_svg]:size-4.5"
                    >
                      {step.icon}
                    </span>
                    <span className="text-xs font-medium text-muted">Step {i + 1}</span>
                  </div>
                  <h3 className="mt-4 font-semibold text-fg">{step.title}</h3>
                  <p className="mt-1.5 text-sm text-muted">{step.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>
      </main>

      <footer className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-2 px-4 py-8 text-xs text-muted sm:px-6">
        <span>CodeLens: AI code review, built as an open portfolio project.</span>
        <Link to="/health" className="hover:text-fg hover:underline">
          System status
        </Link>
      </footer>
    </div>
  )
}

export function LandingPage() {
  const auth = useAuth()
  if (auth.status === 'authenticated') return <Navigate to="/dashboard" replace />
  return <PublicHome />
}
