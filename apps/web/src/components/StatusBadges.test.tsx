import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { RunStatus } from '../api/types'
import { RunStatusBadge } from './StatusBadges'

describe('RunStatusBadge', () => {
  const cases: [RunStatus, boolean, string][] = [
    ['succeeded', true, 'Reviewed'],
    // The M4 stub succeeds without posting anything.
    ['succeeded', false, 'Not reviewed'],
    ['failed', false, 'Failed'],
    ['skipped', false, 'Skipped'],
    ['queued', false, 'Queued'],
    ['running', false, 'Reviewing…'],
    ['superseded', false, 'Superseded'],
  ]
  it.each(cases)('%s with review_posted=%s shows "%s"', (status, posted, label) => {
    render(<RunStatusBadge status={status} reviewPosted={posted} />)
    expect(screen.getByText(label)).toBeInTheDocument()
    if (label !== 'Reviewed') expect(screen.queryByText('Reviewed')).not.toBeInTheDocument()
  })
})
