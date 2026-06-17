import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { RecommendationCard } from '@/features/health/RecommendationCard'
import { RECOMMENDATIONS } from '@/mock/fixtures/maintenance'

const REC = RECOMMENDATIONS[0]

describe('RecommendationCard', () => {
  it('renders the kind, severity, confidence and rationale', () => {
    render(<RecommendationCard rec={REC} />)
    expect(screen.getByText('Compact small files')).toBeInTheDocument()
    expect(screen.getByText('Critical')).toBeInTheDocument()
    expect(screen.getByText(/high confidence/i)).toBeInTheDocument()
    expect(screen.getByText(REC.rationale)).toBeInTheDocument()
  })

  it('shows the remediation command and an explicit "not applied yet" note', () => {
    render(<RecommendationCard rec={REC} />)
    expect(
      screen.getByText(REC.remediation!.command!),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/does not apply maintenance yet/i),
    ).toBeInTheDocument()
  })

  it('shows the table name when showTable is set', () => {
    render(<RecommendationCard rec={REC} showTable />)
    expect(screen.getByText('analytics.events')).toBeInTheDocument()
  })

  it('calls onDismiss with the recommendation id when dismissed', () => {
    const onDismiss = vi.fn()
    render(<RecommendationCard rec={REC} onDismiss={onDismiss} />)
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(onDismiss).toHaveBeenCalledWith(REC.id)
  })

  it('omits the dismiss button when no handler is provided', () => {
    render(<RecommendationCard rec={REC} />)
    expect(
      screen.queryByRole('button', { name: /dismiss/i }),
    ).not.toBeInTheDocument()
  })
})
