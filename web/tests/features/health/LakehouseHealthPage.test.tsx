import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'

const HEALTH_ROUTE = '/acme-analytics/health'

describe('LakehouseHealthPage', () => {
  it('renders the workspace score, table list and recommendations', async () => {
    renderWithProviders({ initialRoute: HEALTH_ROUTE })

    expect(await screen.findByText('Lakehouse health')).toBeInTheDocument()

    // The overall gauge and both scanned tables. The unhealthy table also
    // appears in the recommendation feed, so allow more than one match.
    expect(await screen.findByRole('img', { name: /health score/i })).toBeInTheDocument()
    expect(screen.getAllByText('analytics.events').length).toBeGreaterThan(0)
    expect(screen.getByText('analytics.users')).toBeInTheDocument()

    // The open recommendations feed.
    expect(screen.getByText('Compact small files')).toBeInTheDocument()
    expect(screen.getByText('Expire old snapshots')).toBeInTheDocument()
  })

  it('surfaces how many tables need attention', async () => {
    renderWithProviders({ initialRoute: HEALTH_ROUTE })
    expect(await screen.findByText(/1 need attention/)).toBeInTheDocument()
  })

  it('shows an empty state when no tables have been scanned', async () => {
    server.use(
      http.get('/api/workspaces/:ws/health', () =>
        HttpResponse.json({
          summary: {
            score: null,
            band: 'unknown',
            table_count: 0,
            attention_count: 0,
            total_data_bytes: 0,
          },
          namespaces: [],
          tables: [],
        }),
      ),
    )
    renderWithProviders({ initialRoute: HEALTH_ROUTE })
    expect(await screen.findByText(/no health data yet/i)).toBeInTheDocument()
  })
})
