import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@tests/utils'
import { server } from '@tests/mock/server'

const ROUTE = '/acme-analytics/saved-queries'

describe('SavedQueriesPage', () => {
  it('lists saved queries for the workspace', async () => {
    renderWithProviders({ initialRoute: ROUTE })
    expect(await screen.findByText('Daily events')).toBeInTheDocument()
  })

  it('shows who saved each query', async () => {
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByText('Daily events')
    expect(screen.getAllByText(/Saved by Marton/).length).toBeGreaterThan(0)
  })

  it('shows an empty state when the workspace has no saved queries', async () => {
    server.use(
      http.get('/api/workspaces/:ws/saved-queries', () => HttpResponse.json([])),
    )
    renderWithProviders({ initialRoute: ROUTE })
    expect(
      await screen.findByText('Save a worksheet to keep it here.'),
    ).toBeInTheDocument()
  })

  it('"Open" seeds a worksheet tab and pre-selects the saved agent', async () => {
    // A saved query whose default agent (ag-2 / agent-b) differs from the
    // worksheet default (ag-1 / agent-a), so a pre-selection is observable.
    server.use(
      http.get('/api/workspaces/:ws/saved-queries', () =>
        HttpResponse.json([
          {
            id: 'sq-x',
            name: 'Agent-bound query',
            sql: 'SELECT 99',
            workspace_id: 'ws-1',
            default_agent_id: 'ag-2',
            created_by: 'u-1',
            created_by_name: 'Marton',
            created_at: '2026-05-01T00:00:00Z',
            last_run_at: null,
          },
        ]),
      ),
    )
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: ROUTE })
    await screen.findByText('Agent-bound query')

    await user.click(screen.getByRole('button', { name: 'Open' }))

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/acme-analytics/worksheets')
    })
    // The worksheet consumes the stash on mount, seeds a "saved query" tab and
    // pre-selects the saved default agent.
    expect(await screen.findByText('saved query')).toBeInTheDocument()
    expect((await screen.findAllByText('agent-b')).length).toBeGreaterThan(0)
  })

  it('deletes a saved query', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByText('Daily events')

    await user.click(screen.getByRole('button', { name: 'Delete Daily events' }))
    await user.click(screen.getByRole('button', { name: 'Delete' }))

    await waitFor(() => {
      expect(screen.queryByText('Daily events')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Funnel overview')).toBeInTheDocument()
  })

  it('renames a saved query', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByText('Daily events')

    await user.click(screen.getByRole('button', { name: 'Rename Daily events' }))
    const input = screen.getByLabelText('Name')
    await user.clear(input)
    await user.type(input, 'Weekly events')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('Weekly events')).toBeInTheDocument()
    expect(screen.queryByText('Daily events')).not.toBeInTheDocument()
  })
})
