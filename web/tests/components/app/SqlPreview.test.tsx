import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SqlPreview } from '@/components/app/SqlPreview'

describe('SqlPreview', () => {
  it('shows the full SQL text without needing to expand', () => {
    const sql = 'SELECT * FROM raw.events WHERE event_time >= 2026-05-01'
    render(<SqlPreview sql={sql} />)

    expect(screen.getByText(sql)).toBeInTheDocument()
  })

  it('expands and collapses in place, never opening a dialog', async () => {
    const sql = 'SELECT 1'
    const user = userEvent.setup()
    render(<SqlPreview sql={sql} />)

    expect(screen.getByRole('button', { name: /expand sql/i })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /expand sql/i }))

    expect(screen.getByRole('button', { name: /collapse sql/i })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText(sql)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /collapse sql/i }))

    expect(screen.getByRole('button', { name: /expand sql/i })).toBeInTheDocument()
  })
})
