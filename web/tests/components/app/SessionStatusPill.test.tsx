import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  SessionStatusPill,
  formatCloseReason,
} from '@/components/app/SessionStatusPill'

describe('SessionStatusPill', () => {
  it('renders "pending" — a session whose compute is still starting', () => {
    render(<SessionStatusPill status="pending" />)
    const pill = screen.getByRole('status')
    expect(pill).toHaveTextContent('pending')
    // The colour maps are exhaustive Records; a missing key would land here as a
    // literal "undefined" class rather than failing anywhere visible.
    expect(pill.className).not.toContain('undefined')
  })

  it('renders "open"', () => {
    render(<SessionStatusPill status="open" />)
    expect(screen.getByText('open')).toBeInTheDocument()
  })

  it('renders the cold-start close reasons as prose, never the raw enum', () => {
    expect(formatCloseReason('compute_timeout')).toBe(
      'compute did not start in time',
    )
    expect(formatCloseReason('provisioning_timeout')).toBe(
      'no compute became available',
    )
  })

  it('falls back to readable words for a reason it does not know', () => {
    // @ts-expect-error — a reason the server learned after this build shipped.
    expect(formatCloseReason('some_new_reason')).toBe('some new reason')
  })
})
