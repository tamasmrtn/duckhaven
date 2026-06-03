import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusPill } from '@/components/app/StatusPill'

describe('StatusPill', () => {
  it('renders "queued" label', () => {
    render(<StatusPill status="queued" />)
    expect(screen.getByText('queued')).toBeInTheDocument()
  })

  it('renders "running" label', () => {
    render(<StatusPill status="running" startedAt={new Date().toISOString()} />)
    expect(screen.getByText(/running/)).toBeInTheDocument()
  })

  it('renders "done" with formatted duration', () => {
    render(<StatusPill status="done" durationMs={1400} />)
    expect(screen.getByText(/done/)).toBeInTheDocument()
    expect(screen.getByText(/1\.4s/)).toBeInTheDocument()
  })

  it('renders "done" with ms for sub-second duration', () => {
    render(<StatusPill status="done" durationMs={320} />)
    expect(screen.getByText(/320ms/)).toBeInTheDocument()
  })

  it('renders "failed" label', () => {
    render(<StatusPill status="failed" />)
    expect(screen.getByText('failed')).toBeInTheDocument()
  })

  it('renders "cancelled" label', () => {
    render(<StatusPill status="cancelled" />)
    expect(screen.getByText('cancelled')).toBeInTheDocument()
  })

  it('has role="status" and aria-live="polite"', () => {
    render(<StatusPill status="queued" />)
    const pill = screen.getByRole('status')
    expect(pill).toHaveAttribute('aria-live', 'polite')
  })

  it('exposes the status as an accessible name and title', () => {
    render(<StatusPill status="failed" />)
    const pill = screen.getByRole('status')
    expect(pill).toHaveAttribute('aria-label', 'failed')
    expect(pill).toHaveAttribute('title', 'failed')
  })
})
