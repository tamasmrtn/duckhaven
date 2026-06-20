import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { CatalogTree } from '@/features/catalog/CatalogTree'
import { createWrapper } from '@tests/utils'
import { server } from '@tests/mock/server'

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

  it('expands a table to reveal its columns', async () => {
    renderTree(() => {})

    const eventsName = await screen.findByRole('button', { name: /events/i })
    // Columns are not shown until the row is expanded.
    expect(screen.queryByText('event_id')).not.toBeInTheDocument()

    // The expand toggle sits alongside the table name in the same row.
    const row = eventsName.closest('div') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: /show columns/i }))

    expect(await screen.findByText('event_id')).toBeInTheDocument()
    expect(screen.getByText('event_type')).toBeInTheDocument()
  })

  it('opens the create-schema dialog from the add button', async () => {
    renderTree(() => {})

    await userEvent.click(screen.getByRole('button', { name: /add schema/i }))

    expect(await screen.findByText('New schema')).toBeInTheDocument()
  })

  it('refetches the catalog when the refresh button is clicked', async () => {
    renderTree(() => {})

    // Wait for the initial catalog load.
    await screen.findByRole('button', { name: /events/i })

    // The catalog now reports a schema created out-of-band (e.g. from the
    // worksheet); the refresh button must surface it.
    server.use(
      http.get('/api/workspaces/:ws/schemas', () =>
        HttpResponse.json([{ name: 'fresh_schema', workspace_id: 'x' }]),
      ),
    )

    await userEvent.click(screen.getByRole('button', { name: /refresh catalog/i }))

    expect(
      await screen.findByRole('button', { name: /fresh_schema/i }),
    ).toBeInTheDocument()
  })

  it('probes row-count stats via the refresh endpoint on refresh', async () => {
    let probed = false
    server.use(
      http.post('/api/workspaces/:ws/schemas/refresh-stats', () => {
        probed = true
        return HttpResponse.json({ probed: 1 })
      }),
    )
    renderTree(() => {})
    await screen.findByRole('button', { name: /events/i })

    await userEvent.click(screen.getByRole('button', { name: /refresh catalog/i }))

    await waitFor(() => expect(probed).toBe(true))
  })
})
