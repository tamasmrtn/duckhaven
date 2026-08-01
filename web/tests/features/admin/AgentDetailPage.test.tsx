import { describe, it, expect } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'
import { makeEmptyMonitoring, makeMonitoring } from '@/mock/fixtures/monitoring'

// ag-5 (warehouse-a) is a running elastic agent; ag-1 (agent-a) is static.
const ELASTIC = '/acme-analytics/compute/ag-5'
const STATIC = '/acme-analytics/compute/ag-1'

describe('AgentDetailPage', () => {
  it('opens on Monitoring — the tab the page exists for', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    expect(await screen.findByRole('tab', { name: /monitoring/i })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('renders every chart on the shared time grid', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    expect(await screen.findByTestId('chart-peak-query-count')).toBeInTheDocument()
    expect(screen.getByTestId('chart-completed-query-count')).toBeInTheDocument()
    expect(screen.getByTestId('chart-activity')).toBeInTheDocument()
    expect(screen.getByTestId('chart-utilization')).toBeInTheDocument()
    // The mock's busy stretch produces failures, so this chart renders too.
    expect(screen.getByTestId('chart-failures')).toBeInTheDocument()
  })

  it('defaults to the 8-hour window and reports its bucket size', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    expect(await screen.findByText(/last 8 hours/i)).toBeInTheDocument()
    expect(await screen.findByText(/5-minute buckets/i)).toBeInTheDocument()
  })

  it('refetches with the chosen window and rebuckets', async () => {
    const requested: string[] = []
    server.use(
      http.get('/api/admin/agents/:id/monitoring', ({ request }) => {
        const w = new URL(request.url).searchParams.get('window') ?? '8h'
        requested.push(w)
        return HttpResponse.json(makeMonitoring(w as '1h'))
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ELASTIC })
    await screen.findByTestId('chart-peak-query-count')

    await user.click(screen.getByRole('combobox', { name: /time range/i }))
    await user.click(await screen.findByRole('option', { name: /last 1 hour/i }))

    await waitFor(() => expect(requested).toContain('1h'))
    // A finer window means finer buckets, which is the whole point of switching.
    expect(await screen.findByText(/1-minute buckets/i)).toBeInTheDocument()
  })

  it('offers exactly the five documented windows', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ELASTIC })
    await screen.findByTestId('chart-peak-query-count')

    await user.click(screen.getByRole('combobox', { name: /time range/i }))

    const options = await screen.findAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual([
      'Last 1 hour',
      'Last 3 hours',
      'Last 8 hours',
      'Last 12 hours',
      'Last 24 hours',
    ])
  })

  it('shows live running/queued counts from the 2s sampler', async () => {
    renderWithProviders({ initialRoute: STATIC })

    // The ring buffer answers "right now"; the minute-grained rollup cannot.
    expect(await screen.findByTestId('live-running-queries')).toBeInTheDocument()
    expect(screen.getByTestId('live-queued-queries')).toBeInTheDocument()
  })

  it('summarises uptime, busy share and the idle timeout together', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    // These three only mean something next to each other: the busy share is what
    // says whether the idle timeout is set too generously.
    const activity = await screen.findByText(/^Up .*% busy.*idle timeout/)
    expect(activity).toBeInTheDocument()
  })

  it('labels activity states in the legend, never by colour alone', async () => {
    renderWithProviders({ initialRoute: ELASTIC })
    const chart = await screen.findByTestId('chart-activity')
    const section = chart.closest('section')!

    expect(within(section).getByText('Query activity')).toBeInTheDocument()
    expect(within(section).getByText('Ready')).toBeInTheDocument()
  })

  it('surfaces the utilization peak, not just the bucket average', async () => {
    server.use(
      http.get('/api/admin/agents/:id/monitoring', () =>
        HttpResponse.json({
          ...makeMonitoring('1h'),
          // A one-second allocation spike inside an otherwise-quiet minute:
          // exactly the shape an OOM leaves behind.
          utilization: [
            { t: new Date().toISOString(), cpu_avg: 5, cpu_max: 12, mem_avg: 7, mem_max: 92 },
          ],
        }),
      ),
    )
    renderWithProviders({ initialRoute: ELASTIC })

    const chart = await screen.findByTestId('chart-utilization')
    const section = chart.closest('section')!
    // Averaging buries the spike — an agent that touched 92% would otherwise
    // read as a calm 7% line, right beside the failure it caused.
    expect(within(section).getByText('Memory peak')).toBeInTheDocument()
    expect(within(section).getByText('92%')).toBeInTheDocument()
    expect(within(section).getByText('12%')).toBeInTheDocument()
  })

  it('distinguishes "no recorded history" from downtime', async () => {
    server.use(
      http.get('/api/admin/agents/:id/monitoring', () =>
        HttpResponse.json(makeEmptyMonitoring('8h')),
      ),
    )
    renderWithProviders({ initialRoute: ELASTIC })

    const chart = await screen.findByTestId('chart-activity')
    const section = chart.closest('section')!
    // An agent older than the lifecycle trail must not be drawn as an outage.
    expect(within(section).getByText('No data')).toBeInTheDocument()
    expect(within(section).queryByText('Not running')).not.toBeInTheDocument()
  })

  it('omits the failures chart when nothing failed', async () => {
    server.use(
      http.get('/api/admin/agents/:id/monitoring', () =>
        HttpResponse.json({ ...makeMonitoring('8h'), failures: [] }),
      ),
    )
    renderWithProviders({ initialRoute: ELASTIC })

    await screen.findByTestId('chart-peak-query-count')
    // An empty stacked bar is noise; the chart earns its space only when there
    // is something to explain.
    expect(screen.queryByTestId('chart-failures')).not.toBeInTheDocument()
  })

  it('prompts to widen the range when the window is empty', async () => {
    server.use(
      http.get('/api/admin/agents/:id/monitoring', () =>
        HttpResponse.json(makeEmptyMonitoring('8h')),
      ),
    )
    renderWithProviders({ initialRoute: ELASTIC })

    expect(await screen.findByText(/no queries in this window/i)).toBeInTheDocument()
  })

  it('lists this agent’s runs for the selected window', async () => {
    let params: URLSearchParams | null = null
    server.use(
      http.get('/api/workspaces/:ws/queries', ({ request }) => {
        params = new URL(request.url).searchParams
        return HttpResponse.json([
          {
            id: 'q-1',
            workspace_id: 'ws-1',
            agent_id: 'ag-5',
            user_name: 'Ada',
            sql: 'select 1',
            status: 'done',
            row_count: 1,
            duration_ms: 2000,
            result_bytes: 10,
            error: null,
            started_at: '2026-07-28T10:00:00Z',
            running_at: '2026-07-28T10:00:03Z',
            finished_at: '2026-07-28T10:00:05Z',
          },
        ])
      }),
    )
    renderWithProviders({ initialRoute: ELASTIC })

    expect(await screen.findByText('select 1')).toBeInTheDocument()
    // Scoped to this agent and bounded by the same window the charts drew.
    await waitFor(() => expect(params?.get('agent_id')).toBe('ag-5'))
    expect(params?.get('since')).toBeTruthy()
    expect(params?.get('until')).toBeTruthy()
  })

  it('splits a run’s duration into queue wait and execution', async () => {
    server.use(
      http.get('/api/workspaces/:ws/queries', () =>
        HttpResponse.json([
          {
            id: 'q-slow',
            workspace_id: 'ws-1',
            agent_id: 'ag-5',
            user_name: 'Ada',
            sql: 'select pg_sleep(1)',
            status: 'done',
            row_count: 1,
            duration_ms: 2000,
            result_bytes: 10,
            error: null,
            started_at: '2026-07-28T10:00:00Z',
            running_at: '2026-07-28T10:00:03Z',
            finished_at: '2026-07-28T10:00:05Z',
          },
        ]),
      ),
    )
    renderWithProviders({ initialRoute: ELASTIC })

    // "3s queued, 2s running" is a different problem from "5s of slow SQL".
    const duration = await screen.findByTitle(/queued 3\.0s .* running 2\.0s/i)
    expect(duration).toBeInTheDocument()
  })

  describe('overview tab', () => {
    it('shows the real recent-error count, not a hardcoded zero', async () => {
      server.use(
        http.get('/api/admin/agents/:id/monitoring', () =>
          HttpResponse.json({
            ...makeMonitoring('1h'),
            summary: {
              uptime_s: 3600,
              busy_ratio: 0.5,
              completed: 12,
              failed: 4,
              idle_timeout_minutes: 20,
            },
          }),
        ),
      )
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: ELASTIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))

      const section = (await screen.findByText('Last hour')).closest('section')!
      expect(within(section).getByText('Completed').nextSibling).toHaveTextContent('12')
      expect(within(section).getByText('Failed').nextSibling).toHaveTextContent('4')
    })

    it('restarts a terminated elastic agent', async () => {
      let restarted = false
      server.use(
        http.get('/api/admin/agents/ag-5', () =>
          HttpResponse.json({
            id: 'ag-5',
            name: 'warehouse-a',
            status: 'unavailable',
            capabilities: null,
            last_ping_at: null,
            created_at: new Date().toISOString(),
            provider: 'azure_aci',
            lifecycle: 'terminated',
            requested_cpu: 4,
            requested_memory_gb: 16,
            hourly_cost: 0.28,
            idle_timeout_minutes: 20,
            access_tier: 'operate',
            access_mode: 'open',
          }),
        ),
        http.post('/api/admin/agents/ag-5/restart', () => {
          restarted = true
          return HttpResponse.json({ id: 'ag-5', lifecycle: 'provisioning' }, { status: 202 })
        }),
      )
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: ELASTIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))

      await user.click(await screen.findByRole('button', { name: /restart agent/i }))
      await waitFor(() => expect(restarted).toBe(true))
    })

    it('terminates a running elastic agent', async () => {
      let terminated = false
      server.use(
        http.post('/api/admin/agents/ag-5/terminate', () => {
          terminated = true
          return HttpResponse.json({ id: 'ag-5', lifecycle: 'terminated' }, { status: 202 })
        }),
      )
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: ELASTIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))

      await user.click(await screen.findByRole('button', { name: /^terminate$/i }))
      await waitFor(() => expect(terminated).toBe(true))
    })

    it('warns about the consequences before deleting', async () => {
      let deleted = false
      server.use(
        http.delete('/api/admin/agents/ag-5', () => {
          deleted = true
          return new HttpResponse(null, { status: 204 })
        }),
      )
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: ELASTIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))
      await user.click(await screen.findByRole('button', { name: /^delete$/i }))

      expect(await screen.findByRole('heading', { name: /delete agent/i })).toBeInTheDocument()
      expect(screen.getByText(/cannot be undone/i)).toBeInTheDocument()
      // The monitoring history goes with it — worth saying before, not after.
      expect(screen.getByText(/monitoring history is deleted/i)).toBeInTheDocument()
      expect(deleted).toBe(false)

      await user.click(screen.getByRole('button', { name: /delete permanently/i }))
      await waitFor(() => expect(deleted).toBe(true))
    })

    it('deep-links to the agent-filtered audit history', async () => {
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: ELASTIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))

      await user.click(
        await screen.findByRole('button', { name: /view audit for this agent/i }),
      )

      expect(await screen.findByRole('heading', { name: /history/i })).toBeInTheDocument()
      expect(
        await screen.findByRole('combobox', { name: /filter by agent/i }),
      ).toHaveTextContent('warehouse-a')
    })
  })

  it('recovers gracefully when the agent is gone', async () => {
    server.use(
      http.get('/api/admin/agents/ag-5', () =>
        HttpResponse.json({ detail: 'Agent not found' }, { status: 404 }),
      ),
    )
    renderWithProviders({ initialRoute: ELASTIC })

    expect(await screen.findByText(/agent not found/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /back to agents/i })).toBeInTheDocument()
  })
})

describe('AgentDetailPage per-agent tiers', () => {
  /** Serve ag-5 at a chosen tier, leaving every other handler intact. */
  function agentAtTier(tier: string) {
    return http.get('/api/admin/agents/ag-5', () =>
      HttpResponse.json({
        id: 'ag-5',
        name: 'warehouse-a',
        status: 'healthy',
        capabilities: null,
        last_ping_at: null,
        created_at: '2026-06-01T00:00:00Z',
        provider: 'azure_aci',
        lifecycle: 'running',
        requested_cpu: 4,
        requested_memory_gb: 16,
        hourly_cost: 0.28,
        access_tier: tier,
        access_mode: 'open',
      }),
    )
  }

  it('offers a use-tier holder no action beyond reading the audit', async () => {
    server.use(agentAtTier('use'))
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ELASTIC })
    await user.click(await screen.findByRole('tab', { name: /overview/i }))

    // Monitoring and the audit link are `use`-tier surfaces...
    expect(
      await screen.findByRole('button', { name: /view audit for this agent/i }),
    ).toBeInTheDocument()
    // ...everything that changes the agent is not.
    expect(screen.queryByRole('button', { name: /terminate/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /force disconnect/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^delete$/i })).not.toBeInTheDocument()
  })

  it('gives an operate-tier holder the lifecycle actions but not delete', async () => {
    server.use(agentAtTier('operate'))
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ELASTIC })
    await user.click(await screen.findByRole('tab', { name: /overview/i }))

    expect(await screen.findByRole('button', { name: /terminate/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /force disconnect/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^delete$/i })).not.toBeInTheDocument()
  })

  it('gives an admin-tier holder delete as well', async () => {
    server.use(agentAtTier('admin'))
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ELASTIC })
    await user.click(await screen.findByRole('tab', { name: /overview/i }))

    expect(await screen.findByRole('button', { name: /^delete$/i })).toBeInTheDocument()
  })

  it('force-disconnects an agent', async () => {
    let disconnected = false
    server.use(
      agentAtTier('operate'),
      http.post('/api/admin/agents/ag-5/disconnect', () => {
        disconnected = true
        return HttpResponse.json({ id: 'ag-5', status: 'unavailable' }, { status: 202 })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ELASTIC })
    await user.click(await screen.findByRole('tab', { name: /overview/i }))

    await user.click(await screen.findByRole('button', { name: /force disconnect/i }))
    await waitFor(() => expect(disconnected).toBe(true))
  })
})
