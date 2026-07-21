import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, fireEvent } from '@testing-library/react'
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
        HttpResponse.json([uuidWorkspaceRow]),
      ),
    )

    renderWithProviders({ initialRoute: '/acme-analytics/history' })

    expect(await screen.findByText('SELECT uuid_marker_query')).toBeInTheDocument()
    expect(screen.queryByText('No queries yet.')).not.toBeInTheDocument()
  })

  it('renders the history as a semantic table', async () => {
    // Regression for the migration onto the shared Table primitive.
    renderWithProviders({ initialRoute: '/acme-analytics/history' })
    await screen.findByText(/raw\.users/)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(
      screen.getByRole('columnheader', { name: 'Status' }),
    ).toBeInTheDocument()
  })

  it('scopes history per workspace', async () => {
    // acme-analytics (ws-1) has queries against raw.*; acme-research (ws-2) has
    // the experiments.* query — each workspace shows only its own.
    const analytics = renderWithProviders({ initialRoute: '/acme-analytics/history' })
    expect(await screen.findByText(/raw\.users/)).toBeInTheDocument()
    expect(screen.queryByText(/experiments\.ab_assignments/)).not.toBeInTheDocument()
    analytics.unmount()

    renderWithProviders({ initialRoute: '/acme-research/history' })
    expect(await screen.findByText(/experiments\.ab_assignments/)).toBeInTheDocument()
    expect(screen.queryByText(/raw\.users/)).not.toBeInTheDocument()
  })

  it('shows the empty state for a workspace with no queries', async () => {
    // public (ws-3) has no query history.
    renderWithProviders({ initialRoute: '/public/history' })
    expect(await screen.findByText('No queries yet.')).toBeInTheDocument()
  })

  it('resolves the agent id to its human-readable name', async () => {
    // Fixture rows reference ag-1/ag-2; the agents cache maps those to names.
    renderWithProviders({ initialRoute: '/acme-analytics/history' })
    expect((await screen.findAllByText('agent-a')).length).toBeGreaterThan(0)
  })

  it('falls back to a short id for an unknown agent', async () => {
    server.use(
      http.get('/api/workspaces/:ws/queries', () =>
        HttpResponse.json([
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
        ]),
      ),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history' })
    await screen.findByText('SELECT unknown_agent_marker')
    // Short id (first 8 chars), never the full UUID.
    expect(screen.getByText('deadbeef')).toBeInTheDocument()
    expect(
      screen.queryByText('deadbeef-1111-2222-3333-444455556666'),
    ).not.toBeInTheDocument()
  })

  it('shows a User column with who ran each query', async () => {
    renderWithProviders({ initialRoute: '/acme-analytics/history' })
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
        return HttpResponse.json([
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
        ])
      }),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history' })
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
        HttpResponse.json([
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
        ]),
      ),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history' })
    await screen.findByText('SELECT plain_marker')

    expect(
      screen.queryByRole('columnheader', { name: 'Session' }),
    ).not.toBeInTheDocument()
  })

  it('narrows history to a kind of run with the origin filter', async () => {
    renderWithProviders({ initialRoute: '/acme-analytics/history' })
    // The seeded workspace mixes interactive runs with a session's statements.
    await screen.findByText(/raw\.users/)

    fireEvent.click(screen.getByRole('button', { name: 'Session' }))

    expect(
      await screen.findByText(/analytics\.stg_orders AS SELECT/),
    ).toBeInTheDocument()
    expect(screen.queryByText(/raw\.users/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Interactive' }))
    expect(await screen.findByText(/raw\.users/)).toBeInTheDocument()
    expect(
      screen.queryByText(/analytics\.stg_orders AS SELECT/),
    ).not.toBeInTheDocument()
  })

  it('links a session statement to the session it belonged to', async () => {
    const { router } = renderWithProviders({
      initialRoute: '/acme-analytics/history',
    })
    await screen.findByText(/raw\.users/)

    // The Session column only appears once something in view has a session.
    expect(
      screen.getByRole('columnheader', { name: 'Session' }),
    ).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: /^session / })[0])

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
        HttpResponse.json([
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
        ]),
      ),
    )
    renderWithProviders({ initialRoute: '/acme-analytics/history' })
    await screen.findByText('SELECT member_marker')
    expect(screen.queryByText('All workspaces')).not.toBeInTheDocument()
  })
})
