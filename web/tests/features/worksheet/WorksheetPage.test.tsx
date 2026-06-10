import { describe, it, expect, vi, afterEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse, delay } from 'msw'
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

  it('opens a freshly created workspace with a single empty tab', async () => {
    // The route guard requires the workspace to exist.
    server.use(
      http.get('/api/workspaces/qa-test-workspace', () =>
        HttpResponse.json({
          id: 'ws-new',
          slug: 'qa-test-workspace',
          name: 'QA Test Workspace',
          storage_backend_id: 'sb-1',
          storage_backend_kind: 'object_store',
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

  it('double-clicking Run dispatches only one query', async () => {
    // The dispatch stays in flight (delay) so two clicks land in the same tick,
    // before React re-renders the button to disabled. The synchronous ref lock
    // in runQuery must drop the second click.
    let postCount = 0
    server.use(
      http.post('/api/workspaces/:ws/queries', async () => {
        postCount += 1
        // Keep the first dispatch in flight so the second click lands while the
        // ref lock is still held (mirrors a real rapid double-click).
        await delay(100)
        return HttpResponse.json({ id: 'q-dbl', status: 'queued' }, { status: 202 })
      }),
      http.get('/api/queries/q-dbl', () =>
        HttpResponse.json({
          id: 'q-dbl',
          workspace_id: 'ws-1',
          agent_id: 'ag-1',
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

    // Fire two clicks without awaiting the first, so the second lands while the
    // first dispatch is still pending (the disabled prop has not re-rendered).
    const first = user.click(runBtn)
    const second = user.click(runBtn)
    await Promise.all([first, second])

    // The query becomes active (status surfaces); only one POST was sent.
    await screen.findByText('scanning', undefined, { timeout: 3000 })
    expect(postCount).toBe(1)
  })

  it('disables Run and shows guidance when no agents are connected', async () => {
    server.use(http.get('/api/agents', () => HttpResponse.json([])))
    renderWithProviders({ initialRoute: WS_ROUTE })

    const runBtn = await screen.findByRole('button', { name: /run query/i })
    expect(runBtn).toBeDisabled()

    expect(await screen.findByText(/no agents connected/i)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /add an agent/i })
    expect(link).toHaveAttribute('href', '/acme-analytics/admin/agents')
  })

  it('surfaces a rejected dispatch as a readable error', async () => {
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

  it('runs a multi-statement worksheet sequentially, one dispatch at a time', async () => {
    // Seed a tab whose SQL spans two statements. Under the Monaco stub the
    // editor handle returns the tab's value, so Run splits it and dispatches
    // each statement in turn.
    localStorage.setItem(
      'dh-worksheets-acme-analytics',
      JSON.stringify([
        {
          id: 'tab-1',
          title: 'multi',
          sql: 'CREATE TABLE t AS SELECT 1; SELECT 2',
          dirty: false,
        },
      ]),
    )
    const dispatched: string[] = []
    server.use(
      http.post('/api/workspaces/:ws/queries', async ({ request }) => {
        const body = (await request.json()) as { sql: string }
        dispatched.push(body.sql)
        return HttpResponse.json(
          { id: `q-${dispatched.length}`, status: 'queued' },
          { status: 202 },
        )
      }),
      http.get('/api/queries/:id', ({ params }) =>
        HttpResponse.json({
          id: params.id,
          workspace_id: 'ws-1',
          agent_id: 'ag-1',
          sql: 'x',
          status: 'done',
          row_count: 1,
          duration_ms: 5,
          error: null,
          progress: null,
          started_at: '2026-05-15T10:00:00Z',
          finished_at: '2026-05-15T10:00:00.005Z',
        }),
      ),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: WS_ROUTE })
    const runBtn = await screen.findByRole('button', { name: /run query/i })

    await user.click(runBtn)

    await waitFor(() => expect(dispatched).toHaveLength(2))
    // The first statement runs before the second is dispatched.
    expect(dispatched).toEqual(['CREATE TABLE t AS SELECT 1', 'SELECT 2'])
    expect(await screen.findByText(/statement 2\/2/i)).toBeInTheDocument()
  })

  it('halts the sequence when a statement does not complete', async () => {
    localStorage.setItem(
      'dh-worksheets-acme-analytics',
      JSON.stringify([
        {
          id: 'tab-1',
          title: 'multi',
          sql: 'CREATE TABLE t AS SELECT 1; SELECT 2',
          dirty: false,
        },
      ]),
    )
    let posts = 0
    server.use(
      http.post('/api/workspaces/:ws/queries', () => {
        posts += 1
        return HttpResponse.json(
          { id: `q-${posts}`, status: 'queued' },
          { status: 202 },
        )
      }),
      http.get('/api/queries/:id', ({ params }) =>
        HttpResponse.json({
          id: params.id,
          workspace_id: 'ws-1',
          agent_id: 'ag-1',
          sql: 'x',
          status: 'failed',
          row_count: null,
          duration_ms: 3,
          error: 'boom',
          progress: null,
          started_at: '2026-05-15T10:00:00Z',
          finished_at: '2026-05-15T10:00:00.003Z',
        }),
      ),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: WS_ROUTE })
    const runBtn = await screen.findByRole('button', { name: /run query/i })

    await user.click(runBtn)

    // The first statement fails, so the second is never dispatched.
    expect(await screen.findByText('boom')).toBeInTheDocument()
    expect(posts).toBe(1)
  })
})

describe('WorksheetPage active query persistence', () => {
  afterEach(() => {
    sessionStorage.clear()
  })

  it('rehydrates a persisted active query after a reload', async () => {
    // Simulate a reload while a query was active: the id is in sessionStorage.
    sessionStorage.setItem('dh-active-query-acme-analytics', 'q-persisted')
    server.use(
      http.get('/api/queries/q-persisted', () =>
        HttpResponse.json({
          id: 'q-persisted',
          workspace_id: 'ws-1',
          agent_id: 'ag-1',
          sql: 'SELECT 1',
          status: 'done',
          row_count: 1,
          duration_ms: 7,
          error: null,
          progress: null,
          started_at: '2026-05-15T10:00:00Z',
          finished_at: '2026-05-15T10:00:00.007Z',
        }),
      ),
    )

    renderWithProviders({ initialRoute: WS_ROUTE })

    // The recovered query's status surfaces without the user re-running it.
    await waitFor(() => {
      expect(screen.getByRole('status')).toBeInTheDocument()
    })
  })

  it('shows the materialized result size when a query completes', async () => {
    sessionStorage.setItem('dh-active-query-acme-analytics', 'q-sized')
    server.use(
      http.get('/api/queries/q-sized', () =>
        HttpResponse.json({
          id: 'q-sized',
          workspace_id: 'ws-1',
          agent_id: 'ag-1',
          sql: 'SELECT 1',
          status: 'done',
          row_count: 1,
          duration_ms: 7,
          result_bytes: 2_097_152,
          error: null,
          progress: null,
          started_at: '2026-05-15T10:00:00Z',
          finished_at: '2026-05-15T10:00:00.007Z',
        }),
      ),
    )

    renderWithProviders({ initialRoute: WS_ROUTE })

    expect(await screen.findByText('2.0 MB')).toBeInTheDocument()
  })
})

describe('WorksheetPage responsive', () => {
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
