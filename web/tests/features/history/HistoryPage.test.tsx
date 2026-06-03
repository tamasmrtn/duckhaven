import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@tests/utils'
import { server } from '@tests/mock/server'

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
})
