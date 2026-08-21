import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@tests/utils'
import { server } from '@tests/mock/server'
import { CURRENT_USER } from '@/mock/fixtures/users'

describe('HistoryPage', () => {
  it('lists the current workspace queries (regression: real UUID workspace ids)', async () => {
    // Production workspace ids are UUIDs, not the mock "ws-N" shape that the old
    // startsWith("ws-") filter relied on. The page must render whatever the
    // workspace-scoped endpoint returns.
    const uuidWorkspaceRow = {
      id: 'q-uuid',
      workspace_id: '137f7947-0000-4000-8000-000000000001',
      agent_id: 'ag-1',
      user_id: 'u-1',
      sql: 'SELECT uuid_marker_query',
      status: 'done',
      row_count: 1,
      duration_ms: 12,
      error: null,
      progress: null,
      started_at: '2026-05-15T10:00:00Z',
      finished_at: '2026-05-15T10:00:00.012Z',
    }
    server.use(
      http.get('/api/workspaces/:ws/queries', () =>
        HttpResponse.json({
          items: [uuidWorkspaceRow],
          cursor: null,
          has_more: false,
        }),
      ),
    )

    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })

    expect(await screen.findByText('SELECT uuid_marker_query')).toBeInTheDocument()
    expect(screen.queryByText('No queries match.')).not.toBeInTheDocument()
  })

  it('renders the history as a semantic table', async () => {
    // Regression for the migration onto the shared Table primitive.
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await screen.findByText(/raw\.users/)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(
      screen.getByRole('columnheader', { name: 'Status' }),
    ).toBeInTheDocument()
  })

  it('scopes history per workspace', async () => {
    // acme-analytics (ws-1) has queries against raw.*; acme-research (ws-2) has
    // the experiments.* query — each workspace shows only its own.
    const analytics = renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    expect(await screen.findByText(/raw\.users/)).toBeInTheDocument()
    expect(screen.queryByText(/experiments\.ab_assignments/)).not.toBeInTheDocument()
    analytics.unmount()

    renderWithProviders({ initialRoute: '/acme-research/history?user=all' })
    expect(await screen.findByText(/experiments\.ab_assignments/)).toBeInTheDocument()
    expect(screen.queryByText(/raw\.users/)).not.toBeInTheDocument()
  })

  it('shows the empty state for a workspace with no queries', async () => {
    // public (ws-3) has no query history.
    renderWithProviders({ initialRoute: '/public/history?user=all' })
    expect(await screen.findByText('No queries match.')).toBeInTheDocument()
  })

  it('resolves the agent id to its human-readable name', async () => {
    // Fixture rows reference ag-1/ag-2; the agents cache maps those to names.
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    expect((await screen.findAllByText('agent-a')).length).toBeGreaterThan(0)
  })

  it('falls back to a short id for an unknown agent', async () => {
    server.use(
      http.get('/api/workspaces/:ws/queries', () =>
        HttpResponse.json({
          items: [
          {
            id: 'q-unknown-agent',
            workspace_id: '137f7947-0000-4000-8000-000000000001',
            agent_id: 'deadbeef-1111-2222-3333-444455556666',
            user_id: 'u-1',
            sql: 'SELECT unknown_agent_marker',
            status: 'done',
            row_count: 1,
            duration_ms: 5,
            error: null,
            progress: null,
            started_at: '2026-05-15T10:00:00Z',
            finished_at: '2026-05-15T10:00:00.005Z',
          },
        ],
          cursor: null,
          has_more: false,
        }),
      ),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await screen.findByText('SELECT unknown_agent_marker')
    // Short id (first 8 chars), never the full UUID.
    expect(screen.getByText('deadbeef')).toBeInTheDocument()
    expect(
      screen.queryByText('deadbeef-1111-2222-3333-444455556666'),
    ).not.toBeInTheDocument()
  })

  it('shows a User column with who ran each query', async () => {
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await screen.findByText(/raw\.users/)
    expect(
      screen.getByRole('columnheader', { name: 'User' }),
    ).toBeInTheDocument()
    // Fixture ws-1 queries were run by Marton and Jess.
    expect(screen.getAllByText('Marton').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Jess').length).toBeGreaterThan(0)
  })

  it('lets an admin switch to the cross-workspace view', async () => {
    server.use(
      http.get('/api/workspaces/:ws/queries', ({ request }) => {
        const all =
          new URL(request.url).searchParams.get('all_workspaces') === 'true'
        return HttpResponse.json({
          items: [
          {
            id: all ? 'q-cross' : 'q-scoped',
            workspace_id: all
              ? '137f7947-0000-4000-8000-000000000002'
              : '137f7947-0000-4000-8000-000000000001',
            agent_id: 'ag-1',
            user_id: 'u-1',
            sql: all ? 'SELECT cross_ws_marker' : 'SELECT scoped_marker',
            status: 'done',
            row_count: 1,
            duration_ms: 5,
            error: null,
            progress: null,
            started_at: '2026-05-15T10:00:00Z',
            finished_at: '2026-05-15T10:00:00.005Z',
          },
        ],
          cursor: null,
          has_more: false,
        })
      }),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await screen.findByText('SELECT scoped_marker')
    // No Workspace column while scoped to one workspace.
    expect(screen.queryByText('Workspace')).not.toBeInTheDocument()

    fireEvent.click(screen.getByText('All workspaces'))
    await screen.findByText('SELECT cross_ws_marker')
    expect(screen.getByText('Workspace')).toBeInTheDocument()
  })

  it('keeps the default view unchanged when nothing came from a session', async () => {
    // Regression: the Session column is adaptive, so an ordinary history table
    // must keep exactly the shape it had before sessions existed.
    server.use(
      http.get('/api/workspaces/:ws/queries', () =>
        HttpResponse.json({
          items: [
          {
            id: 'q-plain',
            workspace_id: '137f7947-0000-4000-8000-000000000001',
            agent_id: 'ag-1',
            user_id: 'u-1',
            sql: 'SELECT plain_marker',
            status: 'done',
            row_count: 1,
            duration_ms: 5,
            error: null,
            progress: null,
            started_at: '2026-05-15T10:00:00Z',
            finished_at: '2026-05-15T10:00:00.005Z',
          },
        ],
          cursor: null,
          has_more: false,
        }),
      ),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await screen.findByText('SELECT plain_marker')

    expect(
      screen.queryByRole('columnheader', { name: 'Connection' }),
    ).not.toBeInTheDocument()
  })

  it('narrows history to a kind of run with the origin filter', async () => {
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    // The seeded workspace mixes interactive runs with a session's statements.
    await screen.findByText(/raw\.users/)

    fireEvent.click(screen.getByRole('button', { name: 'Connection' }))

    expect(
      await screen.findByText(/analytics\.stg_orders AS SELECT/),
    ).toBeInTheDocument()
    // The table holds the previous rows while the next page loads rather than
    // collapsing to skeletons, so their departure is awaited, not asserted.
    await waitFor(() =>
      expect(screen.queryByText(/raw\.users/)).not.toBeInTheDocument(),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Interactive' }))
    expect(await screen.findByText(/raw\.users/)).toBeInTheDocument()
    await waitFor(() =>
      expect(
        screen.queryByText(/analytics\.stg_orders AS SELECT/),
      ).not.toBeInTheDocument(),
    )
  })

  it('links a session statement to the session it belonged to', async () => {
    const { router } = renderWithProviders({
      initialRoute: '/acme-analytics/history?user=all',
    })
    await screen.findByText(/raw\.users/)

    // The Connection column only appears once something in view has a session.
    expect(
      screen.getByRole('columnheader', { name: 'Connection' }),
    ).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: /^connection / })[0])

    expect(router.state.location.pathname).toBe(
      '/acme-analytics/sessions/sess-live',
    )
  })

  it('hides the cross-workspace toggle from non-admins', async () => {
    server.use(
      http.get('/api/me', () =>
        HttpResponse.json({ ...CURRENT_USER, role: 'user' }),
      ),
      http.get('/api/workspaces/:ws/queries', () =>
        HttpResponse.json({
          items: [
          {
            id: 'q-member',
            workspace_id: '137f7947-0000-4000-8000-000000000001',
            agent_id: 'ag-1',
            user_id: 'u-2',
            sql: 'SELECT member_marker',
            status: 'done',
            row_count: 1,
            duration_ms: 5,
            error: null,
            progress: null,
            started_at: '2026-05-15T10:00:00Z',
            finished_at: '2026-05-15T10:00:00.005Z',
          },
        ],
          cursor: null,
          has_more: false,
        }),
      ),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await screen.findByText('SELECT member_marker')
    expect(screen.queryByText('All workspaces')).not.toBeInTheDocument()
  })

  it('sends the agent filter to the server rather than filtering the page', async () => {
    // The list endpoint caps what it returns, so filtering that page in the browser
    // showed nothing at all for an agent whose runs were older than the most recent
    // hundred queries cluster-wide. "View audit for this agent" has to ask the server.
    const seen: string[] = []
    server.use(
      http.get('/api/workspaces/:ws/queries', ({ request }) => {
        seen.push(new URL(request.url).search)
        return HttpResponse.json({
          items: [
          {
            id: 'q-from-server',
            workspace_id: '137f7947-0000-4000-8000-000000000001',
            agent_id: 'ag-2',
            user_id: 'u-1',
            sql: 'SELECT server_filtered_marker',
            status: 'done',
            row_count: 1,
            duration_ms: 12,
            error: null,
            progress: null,
            started_at: '2026-05-15T10:00:00Z',
            finished_at: '2026-05-15T10:00:00.012Z',
          },
        ],
          cursor: null,
          has_more: false,
        })
      }),
    )

    renderWithProviders({
      initialRoute: '/acme-analytics/history?agent=ag-2',
    })

    expect(
      await screen.findByText('SELECT server_filtered_marker'),
    ).toBeInTheDocument()
    expect(seen.some((q) => q.includes('agent_id=ag-2'))).toBe(true)
  })

  it('lets a non-admin filter their own workspace history by agent', async () => {
    // agent_id is open to any member (unlike user_id), scoped to their own
    // workspace: a member sends the filter but never all_workspaces.
    const seen: string[] = []
    server.use(
      http.get('/api/me', () =>
        HttpResponse.json({ ...CURRENT_USER, role: 'user' }),
      ),
      http.get('/api/workspaces/:ws/queries', ({ request }) => {
        seen.push(new URL(request.url).search)
        return HttpResponse.json({
          items: [
          {
            id: 'q-member',
            workspace_id: '137f7947-0000-4000-8000-000000000001',
            agent_id: 'ag-1',
            user_id: 'u-2',
            sql: 'SELECT member_marker',
            status: 'done',
            row_count: 1,
            duration_ms: 5,
            error: null,
            progress: null,
            started_at: '2026-05-15T10:00:00Z',
            finished_at: '2026-05-15T10:00:00.005Z',
          },
        ],
          cursor: null,
          has_more: false,
        })
      }),
    )
    renderWithProviders({
      initialRoute: '/acme-analytics/history?agent=ag-1&user=all',
    })
    await screen.findByText('SELECT member_marker')

    expect(seen.some((q) => q.includes('agent_id=ag-1'))).toBe(true)
    expect(seen.some((q) => q.includes('all_workspaces=true'))).toBe(false)
  })

  it('opens the agent picker and sets the URL search param on select', async () => {
    const user = userEvent.setup()
    const { router } = renderWithProviders({
      initialRoute: '/acme-analytics/history?user=all',
    })
    await screen.findByText(/raw\.users/)

    await user.click(await screen.findByRole('combobox', { name: /filter by agent/i }))
    await user.click(await screen.findByRole('option', { name: 'agent-b' }))

    // The agent lands in the URL *beside* the scope already there. Writing the
    // search object wholesale — which is what this page used to do — would drop
    // `user` on the floor every time someone picked an agent.
    expect(router.state.location.search).toEqual({ agent: 'ag-2', user: 'all' })

    await user.click(screen.getByRole('combobox', { name: /filter by agent/i }))
    await user.click(await screen.findByRole('option', { name: 'All agents' }))

    // Cleared values leave the URL; the untouched sibling stays.
    expect(router.state.location.search).toEqual({ user: 'all' })
  })

  it('shows the user filter to an admin regardless of the workspace scope toggle', async () => {
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await screen.findByText(/raw\.users/)

    // Admin, "This workspace" scope (the default) — no need to switch to
    // "All workspaces" first, unlike the old free-text input's gating.
    expect(
      await screen.findByRole('combobox', { name: /filter by user/i }),
    ).toBeInTheDocument()
  })

  it('hides the user filter picker from non-admins', async () => {
    server.use(
      http.get('/api/me', () =>
        HttpResponse.json({ ...CURRENT_USER, role: 'user' }),
      ),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await screen.findByText(/raw\.users/)

    expect(
      screen.queryByRole('combobox', { name: /filter by user/i }),
    ).not.toBeInTheDocument()
  })

  it('filters by user via the picker', async () => {
    const seen: string[] = []
    server.use(
      http.get('/api/workspaces/:ws/queries', ({ request }) => {
        seen.push(new URL(request.url).search)
        return HttpResponse.json({ items: [], cursor: null, has_more: false })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })

    await user.click(await screen.findByRole('combobox', { name: /filter by user/i }))
    await user.click(await screen.findByText('Jess'))

    await waitFor(() =>
      expect(seen.some((q) => q.includes('user_id=u-2'))).toBe(true),
    )
  })

  it('refetches the list when the refresh button is clicked', async () => {
    let calls = 0
    server.use(
      http.get('/api/workspaces/:ws/queries', () => {
        calls += 1
        return HttpResponse.json({ items: [], cursor: null, has_more: false })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await waitFor(() => expect(calls).toBe(1))

    await user.click(screen.getByRole('button', { name: /refresh history/i }))

    await waitFor(() => expect(calls).toBe(2))
  })
})

describe('HistoryPage scope, filters and paging', () => {
  /** Record the query strings the page sends. */
  function recordRequests(items: unknown[] = []) {
    const seen: URLSearchParams[] = []
    server.use(
      http.get('/api/workspaces/:ws/queries', ({ request }) => {
        seen.push(new URL(request.url).searchParams)
        return HttpResponse.json({ items, cursor: null, has_more: false })
      }),
    )
    return seen
  }

  const ROW = {
    id: 'q-row',
    workspace_id: '137f7947-0000-4000-8000-000000000001',
    agent_id: 'ag-1',
    user_id: 'u-1',
    sql: 'SELECT sortable_marker',
    status: 'done',
    row_count: 1,
    duration_ms: 5,
    error: null,
    progress: null,
    started_at: '2026-05-15T10:00:00Z',
    finished_at: '2026-05-15T10:00:00.005Z',
  }

  it("defaults to the current user's runs from the last 7 days", async () => {
    const seen = recordRequests()
    renderWithProviders({ initialRoute: '/acme-analytics/history' })

    await waitFor(() => expect(seen.length).toBeGreaterThan(0))
    const last = seen[seen.length - 1]
    expect(last.get('user_id')).toBe(CURRENT_USER.id)

    const since = last.get('since')
    expect(since).toBeTruthy()
    const days = (Date.now() - Date.parse(since!)) / 86_400_000
    expect(days).toBeGreaterThan(6.9)
    expect(days).toBeLessThan(7.1)
  })

  it('says what it is showing rather than leaving an empty table to interpret', async () => {
    recordRequests()
    renderWithProviders({ initialRoute: '/acme-analytics/history' })

    // The commonest reason for no rows is the default scope, so the empty state
    // names it instead of implying there is no history at all.
    await waitFor(() =>
      expect(screen.getByText(/showing my queries/i)).toBeInTheDocument(),
    )
  })

  it('shows the signed-in user in the picker, matching the default scope', async () => {
    // The picker used to render its "All users" placeholder while the list was
    // in fact scoped to one user, so the control contradicted the rows below it.
    recordRequests()
    renderWithProviders({ initialRoute: '/acme-analytics/history' })

    const picker = await screen.findByRole('combobox', { name: /filter by user/i })
    await waitFor(() => expect(picker).toHaveTextContent(CURRENT_USER.name))
    expect(picker).not.toHaveTextContent('All users')
  })

  it('widens to all users through the picker', async () => {
    const user = userEvent.setup()
    const seen = recordRequests()
    const { router } = renderWithProviders({
      initialRoute: '/acme-analytics/history',
    })

    await user.click(
      await screen.findByRole('combobox', { name: /filter by user/i }),
    )
    await user.click(await screen.findByRole('option', { name: 'All users' }))

    expect(router.state.location.search).toEqual({ user: 'all' })
    await waitFor(() => expect(seen[seen.length - 1].get('user_id')).toBeNull())
  })

  it('restores every filter from the URL', async () => {
    const seen = recordRequests()
    renderWithProviders({
      initialRoute:
        '/acme-analytics/history?user=all&range=all&q=orders&status=failed,cancelled' +
        '&type=select,insert&slower=2&unit=s&sort=duration&dir=asc',
    })

    await waitFor(() => expect(seen.length).toBeGreaterThan(0))
    const p = seen[seen.length - 1]
    expect(p.get('q')).toBe('orders')
    // Comma-joined in the URL, repeated keys on the wire.
    expect(p.getAll('status')).toEqual(['failed', 'cancelled'])
    expect(p.getAll('statement_type')).toEqual(['select', 'insert'])
    expect(p.get('slower_than_ms')).toBe('2000')
    expect(p.get('sort')).toBe('duration')
    expect(p.get('dir')).toBe('asc')
  })

  it('falls back to the default view when a URL parameter is nonsense', async () => {
    const seen = recordRequests()
    renderWithProviders({
      initialRoute:
        '/acme-analytics/history?sort=banana&dir=sideways&range=fortnight&unit=parsec',
    })

    await waitFor(() => expect(seen.length).toBeGreaterThan(0))
    const p = seen[seen.length - 1]
    // A stale or hand-edited link renders the default view rather than an error
    // page. The server still rejects a bad value outright.
    expect(p.get('sort')).toBe('started_at')
    expect(p.get('dir')).toBe('desc')
    expect(p.get('user_id')).toBe(CURRENT_USER.id)
  })

  it('sends a typed search to the server and puts it in the URL', async () => {
    const user = userEvent.setup()
    const seen = recordRequests()
    const { router } = renderWithProviders({
      initialRoute: '/acme-analytics/history?user=all',
    })

    await user.type(
      await screen.findByRole('textbox', { name: /search sql/i }),
      'orders',
    )

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ q: 'orders' }),
    )
    await waitFor(() => expect(seen[seen.length - 1].get('q')).toBe('orders'))
  })

  it('filters by status through a multi-select', async () => {
    const user = userEvent.setup()
    const seen = recordRequests()
    const { router } = renderWithProviders({
      initialRoute: '/acme-analytics/history?user=all',
    })

    await user.click(await screen.findByRole('button', { name: 'status' }))
    await user.click(await screen.findByRole('menuitemcheckbox', { name: 'failed' }))
    await user.click(
      await screen.findByRole('menuitemcheckbox', { name: 'cancelled' }),
    )

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        status: 'failed,cancelled',
      }),
    )
    await waitFor(() =>
      expect(seen[seen.length - 1].getAll('status')).toEqual([
        'failed',
        'cancelled',
      ]),
    )
  })

  it('converts the duration filter to milliseconds using the unit', async () => {
    const user = userEvent.setup()
    const seen = recordRequests()
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })

    await user.type(
      await screen.findByRole('spinbutton', { name: /slower than/i }),
      '5',
    )

    await waitFor(() =>
      expect(seen[seen.length - 1].get('slower_than_ms')).toBe('5000'),
    )
  })

  it('sorts on the server and toggles direction on a second click', async () => {
    const user = userEvent.setup()
    const seen = recordRequests([ROW])
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })

    await user.click(
      await screen.findByRole('button', { name: /sort by duration/i }),
    )
    await waitFor(() => {
      const p = seen[seen.length - 1]
      expect(p.get('sort')).toBe('duration')
      expect(p.get('dir')).toBe('desc')
    })

    await user.click(
      await screen.findByRole('button', { name: /sort by duration/i }),
    )
    await waitFor(() => expect(seen[seen.length - 1].get('dir')).toBe('asc'))
  })

  it('loads another page on demand and appends it', async () => {
    const user = userEvent.setup()
    server.use(
      http.get('/api/workspaces/:ws/queries', ({ request }) => {
        const cursor = new URL(request.url).searchParams.get('cursor')
        const row = (id: string, sql: string) => ({ ...ROW, id, sql })
        return cursor
          ? HttpResponse.json({
              items: [row('q-p2', 'SELECT page_two_marker')],
              cursor: null,
              has_more: false,
            })
          : HttpResponse.json({
              items: [row('q-p1', 'SELECT page_one_marker')],
              cursor: 'cursor-1',
              has_more: true,
            })
      }),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })

    expect(await screen.findByText('SELECT page_one_marker')).toBeInTheDocument()
    // The count states what the page knows, not a total it has not counted.
    expect(screen.getByText('Showing 1 query')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByText('SELECT page_two_marker')).toBeInTheDocument()
    // Appended, not replaced.
    expect(screen.getByText('SELECT page_one_marker')).toBeInTheDocument()
    expect(screen.getByText('All 2 queries')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
  })

  it('clears every filter back to the default view', async () => {
    const user = userEvent.setup()
    const { router } = renderWithProviders({
      initialRoute: '/acme-analytics/history?user=all&q=orders&status=failed',
    })

    await user.click(await screen.findByRole('button', { name: /clear filters/i }))
    expect(router.state.location.search).toEqual({})
  })

  it('does not send a request until it knows whose runs to ask for', async () => {
    // A disabled TanStack query reports isLoading false, so the page used to
    // fall through to the empty state and flash "No queries match" before the
    // skeletons appeared.
    recordRequests()
    renderWithProviders({ initialRoute: '/acme-analytics/history' })
    expect(screen.queryByText('No queries match.')).not.toBeInTheDocument()
  })

  it('debounces the duration filter instead of firing per keystroke', async () => {
    const user = userEvent.setup()
    const seen = recordRequests()
    renderWithProviders({ initialRoute: '/acme-analytics/history?user=all' })
    await waitFor(() => expect(seen.length).toBeGreaterThan(0))
    const before = seen.length

    await user.type(
      await screen.findByRole('spinbutton', { name: /slower than/i }),
      '1500',
    )

    await waitFor(() =>
      expect(seen[seen.length - 1].get('slower_than_ms')).toBe('1500000'),
    )
    // Four keystrokes must not mean four round trips for 1, 15, 150, 1500.
    expect(seen.length - before).toBeLessThan(4)
  })

  it('round-trips a custom range through local time without drifting', async () => {
    const user = userEvent.setup()
    const seen = recordRequests()
    const { router } = renderWithProviders({
      initialRoute: '/acme-analytics/history?user=all&range=custom',
    })

    const from = await screen.findByLabelText('from')
    await user.type(from, '2026-08-01T10:00')

    // Stored as UTC...
    await waitFor(() => {
      const since = (router.state.location.search as { since?: string }).since
      expect(since).toBeTruthy()
      expect(Date.parse(since!)).toBe(new Date('2026-08-01T10:00').getTime())
    })
    // ...and redisplayed as the same local wall clock, not shifted by the
    // offset. Slicing the ISO string put the UTC hour in a local-time field.
    await waitFor(() => expect(from).toHaveValue('2026-08-01T10:00'))
    expect(seen.length).toBeGreaterThan(0)
  })

  it('never shows maintenance probe rows', async () => {
    // The Lakehouse-health scanner writes a `SELECT 1` per scanned table per
    // cycle. A "my queries" default hides them by accident because they carry no
    // user; widening the scope must not bring them flooding back.
    renderWithProviders({
      initialRoute: '/acme-analytics/history?user=all&range=all',
    })
    await screen.findByText(/raw\.users/)
    expect(screen.queryByText('SELECT 1')).not.toBeInTheDocument()
  })
})
