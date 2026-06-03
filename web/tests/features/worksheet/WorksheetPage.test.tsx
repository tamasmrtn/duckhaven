import { describe, it, expect, vi, afterEach } from 'vitest'
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

  it('opens a freshly created workspace with a single empty tab (Bug #9)', async () => {
    // The route guard requires the workspace to exist.
    server.use(
      http.get('/api/workspaces/qa-test-workspace', () =>
        HttpResponse.json({
          id: 'ws-new',
          slug: 'qa-test-workspace',
          name: 'QA Test Workspace',
          storage_backend_id: 'sb-1',
          storage_backend_kind: 'local_fs',
          created_at: new Date().toISOString(),
        }),
      ),
    )
    renderWithProviders({ initialRoute: '/qa-test-workspace/worksheets' })

    expect(await screen.findByText('untitled')).toBeInTheDocument()
    expect(screen.queryByText('events.sql')).not.toBeInTheDocument()
    expect(screen.queryByText('funnel-draft')).not.toBeInTheDocument()
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

  it('disables Run and shows guidance when no agents are connected (Bug #4)', async () => {
    server.use(http.get('/api/agents', () => HttpResponse.json([])))
    renderWithProviders({ initialRoute: WS_ROUTE })

    const runBtn = await screen.findByRole('button', { name: /run query/i })
    expect(runBtn).toBeDisabled()

    expect(await screen.findByText(/no agents connected/i)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /add an agent/i })
    expect(link).toHaveAttribute('href', '/acme-analytics/admin/agents')
  })

  it('surfaces a rejected dispatch as a readable error (BUG-5)', async () => {
    // A disallowed query returns a structured 422; the worksheet must show the
    // human-readable message, never "[object Object]".
    server.use(
      http.post('/api/workspaces/:ws/queries', () =>
        HttpResponse.json(
          { detail: { error: 'sql_not_allowed', detail: 'Disallowed statement type(s): SET' } },
          { status: 422 },
        ),
      ),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: WS_ROUTE })
    const runBtn = await screen.findByRole('button', { name: /run query/i })

    await user.click(runBtn)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Disallowed statement type(s): SET')
    expect(screen.queryByText('[object Object]')).not.toBeInTheDocument()
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

describe('WorksheetPage responsive (Bug #6)', () => {
  const originalMatchMedia = window.matchMedia
  afterEach(() => {
    window.matchMedia = originalMatchMedia
  })

  function mockViewport(isMobile: boolean) {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: isMobile,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia
  }

  it('shows a catalog drawer trigger on narrow screens', async () => {
    mockViewport(true)
    renderWithProviders({ initialRoute: WS_ROUTE })
    expect(
      await screen.findByRole('button', { name: /show tables/i }),
    ).toBeInTheDocument()
  })

  it('renders the inline catalog without a drawer trigger on wide screens', async () => {
    mockViewport(false)
    renderWithProviders({ initialRoute: WS_ROUTE })
    await screen.findByText('events.sql')
    expect(
      screen.queryByRole('button', { name: /show tables/i }),
    ).not.toBeInTheDocument()
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
