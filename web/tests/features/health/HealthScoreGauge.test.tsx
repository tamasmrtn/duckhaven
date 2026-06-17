import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { HealthScoreGauge } from '@/features/health/HealthScoreGauge'

describe('HealthScoreGauge', () => {
  it('renders the score and its band label', () => {
    render(<HealthScoreGauge score={58} band="attention" />)
    expect(screen.getByText('58')).toBeInTheDocument()
    expect(screen.getByText('Needs attention')).toBeInTheDocument()
  })

  it('exposes an accessible label describing the score and band', () => {
    render(<HealthScoreGauge score={96} band="healthy" />)
    const gauge = screen.getByRole('img')
    expect(gauge).toHaveAttribute(
      'aria-label',
      'Health score 96 of 100, Healthy',
    )
  })

  it('shows a dash and "No data" when the score is null', () => {
    render(<HealthScoreGauge score={null} band="unknown" />)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.getByText('No data')).toBeInTheDocument()
    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      'Health score unknown of 100, No data',
    )
  })

  // Regression: the band label used to share the donut's centred overlay, so the
  // longest label ("Needs attention") overflowed the inner hole and overlapped
  // the ring — most visibly at the smaller size=120 used in the table panel.
  // Keeping the label outside the donut box is what guarantees no overlap at any
  // size/zoom/breakpoint, so assert that structural separation here.
  it('keeps the status label outside the donut visualization', () => {
    render(<HealthScoreGauge score={58} band="attention" size={120} />)
    const viz = screen.getByTestId('gauge-viz')
    // The score stays inside the donut hole; the band label must not.
    expect(viz).toContainElement(screen.getByText('58'))
    expect(viz).not.toContainElement(screen.getByText('Needs attention'))
  })
})
