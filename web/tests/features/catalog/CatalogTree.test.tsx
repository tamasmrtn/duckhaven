import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CatalogTree } from '@/features/catalog/CatalogTree'
import { createWrapper } from '@tests/utils'

function renderTree(onTableClick: (schema: string, table: string) => void) {
  const { wrapper: Wrapper } = createWrapper()
  return render(
    <CatalogTree
      ws="acme-analytics"
      workspaceName="acme-analytics"
      onTableClick={onTableClick}
    />,
    { wrapper: Wrapper },
  )
}

describe('CatalogTree', () => {
  it('reports the schema and table when a table row is clicked', async () => {
    const onTableClick = vi.fn()
    renderTree(onTableClick)

    const events = await screen.findByRole('button', { name: /events/i })
    fireEvent.click(events)
    expect(onTableClick).toHaveBeenCalledWith('raw', 'events')
  })

  it('filters table rows by the search box', async () => {
    renderTree(() => {})

    // Schemas auto-expand, so sibling tables are visible up front.
    await screen.findByRole('button', { name: /events/i })
    expect(
      screen.getByRole('button', { name: /page_views/i }),
    ).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Search tables'), 'events')

    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: /page_views/i }),
      ).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /events/i })).toBeInTheDocument()
  })
})
