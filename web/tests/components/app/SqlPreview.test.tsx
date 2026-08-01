import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SqlPreview } from '@/components/app/SqlPreview'

describe('SqlPreview', () => {
  it('shows the SQL inline and expands it to a dialog with the full text', async () => {
    const sql = 'SELECT * FROM raw.events WHERE event_time >= 2026-05-01'
    const user = userEvent.setup()
    render(<SqlPreview sql={sql} />)

    // Visible without expanding — this is a capped-height block, not a modal-only view.
    expect(screen.getAllByText(sql).length).toBeGreaterThan(0)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /expand sql/i }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(sql)
  })

  it('does not trigger a parent click handler when the expand button is clicked', async () => {
    const sql = 'SELECT 1'
    const user = userEvent.setup()
    let parentClicked = false
    render(
      <div onClick={() => (parentClicked = true)}>
        <SqlPreview sql={sql} />
      </div>,
    )

    await user.click(screen.getByRole('button', { name: /expand sql/i }))

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(parentClicked).toBe(false)
  })
})
