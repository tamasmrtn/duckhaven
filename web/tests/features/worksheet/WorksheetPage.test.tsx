import { describe, it, expect } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@tests/utils'
import { server } from '@tests/mock/server'

const WS_ROUTE = '/acme-analytics/worksheets'

describe('WorksheetPage tabs', () => {
  it('renders the two default tabs', async () => {
    renderWithProviders({ initialRoute: WS_ROUTE })
    expect(await screen.findByText('events.sql')).toBeInTheDocument()
    expect(screen.getByText('funnel-draft')).toBeInTheDocument()
  })

  it('shows a dirty indicator on the funnel-draft tab', async () => {
    renderWithProviders({ initialRoute: WS_ROUTE })
    await screen.findByText('funnel-draft')
    expect(screen.getByLabelText('unsaved')).toBeInTheDocument()
  })

  it('adds an untitled tab when the + button is clicked', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: WS_ROUTE })
    await screen.findByText('events.sql')

    await user.click(screen.getByLabelText('New worksheet'))
    expect(screen.getByText('untitled')).toBeInTheDocument()
  })

  it('removes a tab when the close button is clicked', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: WS_ROUTE })
    await screen.findByText('events.sql')

    await user.click(screen.getByLabelText('Close events.sql'))
    expect(screen.queryByText('events.sql')).not.toBeInTheDocument()
    expect(screen.getByText('funnel-draft')).toBeInTheDocument()
  })
})

describe('WorksheetPage run', () => {
  it('dispatches a query when Run is clicked and shows a status pill', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: WS_ROUTE })
    const runBtn = await screen.findByRole('button', { name: /run query/i })

    await user.click(runBtn)

    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument()
    })
  })

  it('shows the running progress stage in the results header', async () => {
    server.use(
      http.post('/api/workspaces/:ws/queries', () =>
        HttpResponse.json({ id: 'q-prog', status: 'queued' }, { status: 202 }),
      ),
      http.get('/api/queries/:id', () =>
        HttpResponse.json({
          id: 'q-prog',
          workspace_id: 'ws',
          agent_id: 'a',
          sql: 'SELECT 1',
          status: 'running',
          row_count: null,
          duration_ms: null,
          error: null,
          progress: { stage: 'scanning' },
          started_at: new Date().toISOString(),
          finished_at: null,
        }),
      ),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: WS_ROUTE })
    const runBtn = await screen.findByRole('button', { name: /run query/i })

    await user.click(runBtn)

    expect(await screen.findByText('scanning')).toBeInTheDocument()
  })
})

describe('WorksheetPage save modal', () => {
  it('opens save dialog and submits a query name', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: WS_ROUTE })
    await screen.findByText('events.sql')

    await user.click(screen.getByRole('button', { name: /save…/i }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Save query')).toBeInTheDocument()

    const nameInput = within(dialog).getByPlaceholderText(/my query name/i)
    await user.type(nameInput, 'Test query')
    await user.click(within(dialog).getByRole('button', { name: /^save$/i }))

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })
  })
})
