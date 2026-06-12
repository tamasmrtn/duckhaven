import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'
import { METRICS } from '@/mock/fixtures/metrics'

const METRICS_ROUTE = '/acme-analytics/admin/metrics'

describe('MetricsPage', () => {
  it('renders a current-value card and charts per reporting agent', async () => {
    renderWithProviders({ initialRoute: METRICS_ROUTE })

    // One current-value card per agent.
    for (const agent of METRICS) {
      expect(await screen.findByText(agent.name)).toBeInTheDocument()
    }
    // Both utilization charts are present.
    expect(screen.getByTestId('chart-cpu_percent')).toBeInTheDocument()
    expect(screen.getByTestId('chart-memory_percent')).toBeInTheDocument()
  })

  it('shows the reporting-agent count', async () => {
    renderWithProviders({ initialRoute: METRICS_ROUTE })
    await screen.findByText(`${METRICS.length} agents reporting`)
  })

  it('shows aggregate running and queued query counters', async () => {
    renderWithProviders({ initialRoute: METRICS_ROUTE })

    // Aggregate of each agent's latest sample across the fixture agents.
    const totals = METRICS.reduce(
      (acc, agent) => {
        const latest = agent.samples[agent.samples.length - 1]
        acc.running += latest.running_queries
        acc.queued += latest.queued_queries
        return acc
      },
      { running: 0, queued: 0 },
    )

    const running = await screen.findByText('Running queries')
    expect(running.parentElement).toHaveTextContent(String(totals.running))
    const queued = screen.getByText('Queued queries')
    expect(queued.parentElement).toHaveTextContent(String(totals.queued))
  })

  it('shows an empty state when no agents are reporting', async () => {
    server.use(
      http.get('/api/admin/agents/metrics', () => HttpResponse.json([])),
    )
    renderWithProviders({ initialRoute: METRICS_ROUTE })
    expect(
      await screen.findByText(/no live utilization/i),
    ).toBeInTheDocument()
  })
})
