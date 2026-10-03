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
  it('puts its tabs in the toolbar row under the header', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    const tablist = await screen.findByRole('tablist')
    expect(tablist.parentElement).toHaveClass('border-b')
  })

  it('titles the page with the agent name in the shared page header', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'warehouse-a' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /back to agents/i })).toBeInTheDocument()
  })

  it('opens on Monitoring — the tab the page exists for', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    expect(await screen.findByRole('tab', { name: /monitoring/i })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('renders every panel on the shared time grid', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    for (const id of ['timeline', 'queries', 'concurrency', 'cpu', 'memory']) {
      expect(await screen.findByTestId(`chart-${id}`)).toBeInTheDocument()
    }
    // The old per-bucket flag charts are gone.
    expect(screen.queryByTestId('chart-peak-query-count')).not.toBeInTheDocument()
    expect(screen.queryByTestId('chart-activity')).not.toBeInTheDocument()
  })

  it('defaults to the 8-hour range and reports its bucket size', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    expect(await screen.findByText(/last 8 hours/i)).toBeInTheDocument()
    expect(await screen.findByText(/5-minute buckets/i)).toBeInTheDocument()
  })

  it('offers every preset up to the week that is retained', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ELASTIC })
    await screen.findByTestId('chart-timeline')

    await user.click(screen.getByRole('combobox', { name: /time range/i }))

    const options = await screen.findAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual([
      'Last 1 hour',
      'Last 3 hours',
      'Last 8 hours',
      'Last 12 hours',
      'Last 24 hours',
      'Last 3 days',
      'Last 7 days',
    ])
  })

  it('refetches with the chosen range and rebuckets', async () => {
    const requested: string[] = []
    server.use(
      http.get('/api/admin/agents/:id/monitoring', ({ request }) => {
        const w = new URL(request.url).searchParams.get('window') ?? '8h'
        requested.push(w)
        return HttpResponse.json(makeMonitoring({ window: w as '7d' }))
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ELASTIC })
    await screen.findByTestId('chart-timeline')

    await user.click(screen.getByRole('combobox', { name: /time range/i }))
    await user.click(await screen.findByRole('option', { name: /last 7 days/i }))

    await waitFor(() => expect(requested).toContain('7d'))
    expect(await screen.findByText(/2-hour buckets/i)).toBeInTheDocument()
  })

  it('opens a zoomed range from the URL, with a way back', async () => {
    const seen: URLSearchParams[] = []
    server.use(
      http.get('/api/admin/agents/:id/monitoring', ({ request }) => {
        const q = new URL(request.url).searchParams
        seen.push(q)
        return HttpResponse.json(
          makeMonitoring(
            q.get('start')
              ? { start: q.get('start')!, end: q.get('end')! }
              : { window: '8h' },
          ),
        )
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({
      initialRoute: `${ELASTIC}?from=2026-09-30T10:00:00.000Z&to=2026-09-30T11:00:00.000Z`,
    })

    await waitFor(() => expect(seen.at(-1)?.get('start')).toBe('2026-09-30T10:00:00.000Z'))
    expect(seen.at(-1)?.get('window')).toBeNull()

    await user.click(await screen.findByRole('button', { name: /reset zoom/i }))
    await waitFor(() => expect(seen.at(-1)?.get('start')).toBeNull())
  })

  it('shows what is executing now, with idle connections beside it, not in it', async () => {
    server.use(
      http.get('/api/admin/agents/metrics', () =>
        HttpResponse.json([
          {
            agent_id: 'ag-1',
            name: 'agent-a',
            samples: [
              {
                cpu_percent: 34,
                memory_percent: 40,
                running_queries: 3,
                queued_queries: 0,
                active_profile: 'auto',
                executing_queries: 1,
                idle_sessions: 2,
                sampled_at: new Date().toISOString(),
              },
            ],
          },
        ]),
      ),
    )
    renderWithProviders({ initialRoute: STATIC })

    await waitFor(() => expect(screen.getByTestId('live-executing')).toHaveTextContent('1'))
    expect(screen.getByText('+2 idle connections')).toBeInTheDocument()
    expect(screen.getByTestId('live-cpu')).toHaveTextContent('34%')
  })

  it('reads "—" for what an older agent cannot measure, never 0', async () => {
    server.use(
      http.get('/api/admin/agents/metrics', () =>
        HttpResponse.json([
          {
            agent_id: 'ag-1',
            name: 'agent-a',
            samples: [
              {
                cpu_percent: 5,
                memory_percent: 10,
                running_queries: 1,
                queued_queries: 0,
                active_profile: 'auto',
                sampled_at: new Date().toISOString(),
              },
            ],
          },
        ]),
      ),
    )
    renderWithProviders({ initialRoute: STATIC })

    await waitFor(() => expect(screen.getByTestId('live-cpu')).toHaveTextContent('5%'))
    expect(screen.getByTestId('live-executing')).toHaveTextContent('—')
  })

  it('summarises the range without advising on the idle timeout', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    const summary = await screen.findByLabelText(/range summary/i)
    for (const label of ['Up', 'Busy', 'Idle', 'Queries', 'p95 wait', 'Peak memory']) {
      expect(within(summary).getByText(label)).toBeInTheDocument()
    }
    expect(screen.queryByText(/shorter idle timeout/i)).not.toBeInTheDocument()
  })

  it('labels timeline states in the legend, never by colour alone', async () => {
    renderWithProviders({ initialRoute: ELASTIC })
    const section = (await screen.findByTestId('chart-timeline')).closest('section')!

    expect(within(section).getByText('Busy')).toBeInTheDocument()
    expect(within(section).getByText('Idle')).toBeInTheDocument()
    expect(within(section).getByText('Not running')).toBeInTheDocument()
  })

  it('distinguishes "no record" from downtime', async () => {
    server.use(
      http.get('/api/admin/agents/:id/monitoring', () =>
        HttpResponse.json(makeEmptyMonitoring({ window: '8h' })),
      ),
    )
    renderWithProviders({ initialRoute: ELASTIC })

    const section = (await screen.findByTestId('chart-timeline')).closest('section')!
    expect(within(section).getByText('No record')).toBeInTheDocument()
    expect(within(section).queryByText('Not running')).not.toBeInTheDocument()
  })

  it('shows "—", not 0 %, for a resource nothing measured', async () => {
    server.use(
      http.get('/api/admin/agents/:id/monitoring', () =>
        HttpResponse.json(makeEmptyMonitoring({ window: '8h' })),
      ),
    )
    renderWithProviders({ initialRoute: ELASTIC })

    const section = (await screen.findByTestId('chart-memory')).closest('section')!
    expect(within(section).getByText('Peak').nextSibling).toHaveTextContent('—')
    expect(within(section).queryByText('0%')).not.toBeInTheDocument()
  })

  it('surfaces the memory peak and marks out-of-memory events', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    const section = (await screen.findByTestId('chart-memory')).closest('section')!
    const data = makeMonitoring({ window: '8h' })
    expect(within(section).getByText('Peak').nextSibling).toHaveTextContent(
      `${Math.round(data.summary.mem_peak!)}%`,
    )
    expect(within(section).getByText(/OOM: out-of-memory/)).toBeInTheDocument()
  })

  it('separates the query’s own SQL errors from the platform’s failures', async () => {
    renderWithProviders({ initialRoute: ELASTIC })

    const section = (await screen.findByTestId('chart-queries')).closest('section')!
    expect(within(section).getByText('SQL error')).toBeInTheDocument()
    expect(within(section).getByText('Failed')).toBeInTheDocument()
    expect(within(section).getByText(/Failed by cause: .*queue full/)).toBeInTheDocument()
  })

  it('lists the agent’s runs in the range, sortable by what they cost', async () => {
    const requests: URLSearchParams[] = []
    server.use(
      http.get('/api/admin/agents/:id/queries', ({ request }) => {
        requests.push(new URL(request.url).searchParams)
        return HttpResponse.json({
          items: [
            {
              id: 'q-1',
              workspace_id: 'ws-1',
              user_name: 'Ada',
              sql: 'select big',
              status: 'done',
              origin: null,
              statement_type: 'SELECT',
              started_at: '2026-07-28T10:00:00Z',
              running_at: '2026-07-28T10:00:03Z',
              finished_at: '2026-07-28T10:00:05Z',
              duration_ms: 2000,
              wait_ms: 3000,
              row_count: 1,
              error: null,
              failure_reason: null,
              peak_memory_bytes: 3 * 1024 ** 3,
              cpu_time_ms: 1500,
              spill_bytes: 0,
              bytes_read: 0,
            },
          ],
          cursor: null,
          has_more: false,
        })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ELASTIC })

    expect(await screen.findByText('select big')).toBeInTheDocument()
    expect(screen.getByText('3.0 GB')).toBeInTheDocument()
    // "3s queued, 2s running" is a different problem from "5s of slow SQL".
    expect(screen.getByTitle(/queued 3\.0s .* running 2\.0s/i)).toBeInTheDocument()
    expect(requests[0].get('start')).toBeTruthy()
    expect(requests[0].get('sort')).toBe('started_at')

    await user.click(screen.getByRole('combobox', { name: /sort queries/i }))
    await user.click(await screen.findByRole('option', { name: /most memory/i }))
    await waitFor(() => expect(requests.at(-1)?.get('sort')).toBe('peak_memory'))
  })

  describe('overview tab', () => {
    it('shows the real recent-error count, not a hardcoded zero', async () => {
      server.use(
        http.get('/api/admin/agents/:id/monitoring', () =>
          HttpResponse.json({
            ...makeMonitoring({ window: '1h' }),
            summary: {
              ...makeMonitoring({ window: '1h' }).summary,
              finished: 12,
              failed: 4,
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

    it('shows the runtime, exact engine and whether the sandbox lock applied', async () => {
      server.use(
        http.get('/api/admin/agents/ag-1', () =>
          HttpResponse.json({
            id: 'ag-1',
            name: 'agent-a',
            status: 'healthy',
            capabilities: {
              duckdb_version: '1.5.5 (with duckdb 1.5.5)',
              engine_version: 'v1.5.5',
              runtime_id: '1.5',
              sandbox: 'failed',
              extensions: ['httpfs', 'iceberg'],
              memory_limit_gb: 6,
              cores: 4,
              host: 'homeserver-01',
            },
            last_ping_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            access_tier: 'admin',
            access_mode: 'open',
            runtime: { id: '1.5', display_name: 'DuckDB 1.5', status: 'ga', state: 'ok', default: true },
          }),
        ),
      )
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: STATIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))

      const section = (await screen.findByText('Capabilities')).closest('section')!
      expect(within(section).getByText('Runtime').nextSibling).toHaveTextContent('DuckDB 1.5')
      expect(within(section).getByText('DuckDB').nextSibling).toHaveTextContent('v1.5.5')
      expect(within(section).getByText('Sandbox').nextSibling).toHaveTextContent(
        'lock failed — no SQL sessions',
      )
    })

    it('lists every extension in full rather than truncating the line', async () => {
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: STATIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))

      const list = await screen.findByRole('list', { name: 'Extensions' })
      // agent-a (ag-1) advertises three extensions; each is its own item.
      expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
        'iceberg',
        'httpfs',
        'azure',
      ])
    })

    it('explains each capability from the ⓘ beside it', async () => {
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: STATIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))

      for (const label of ['Runtime', 'DuckDB', 'Memory cap', 'Cores', 'Host', 'Extensions']) {
        expect(
          await screen.findByRole('button', { name: `What is ${label}?` }),
        ).toBeInTheDocument()
      }
      // Keyboard focus opens it, not only a mouse hover.
      screen.getByRole('button', { name: 'What is Extensions?' }).focus()
      expect(
        (await screen.findAllByText(/httpfs for S3 and the bundled object store/)).length,
      ).toBeGreaterThan(0)
    })

    it('warns about a deprecated runtime and names its upstream end of support', async () => {
      server.use(
        http.get('/api/runtimes', () =>
          HttpResponse.json([
            {
              id: '1.4',
              display_name: 'DuckDB 1.4',
              duckdb_line: '1.4',
              status: 'deprecated',
              extensions: [],
              ducklake_format: null,
              upstream_eol: '2026-11-17',
              default: false,
            },
          ]),
        ),
        http.get('/api/admin/agents/ag-1', () =>
          HttpResponse.json({
            id: 'ag-1',
            name: 'agent-a',
            status: 'healthy',
            capabilities: { duckdb_version: '1.4.3', extensions: [], memory_limit_gb: 6, cores: 4 },
            last_ping_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            access_tier: 'admin',
            runtime: { id: '1.4', display_name: 'DuckDB 1.4', status: 'deprecated', state: 'ok' },
          }),
        ),
      )
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: STATIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))

      expect(
        await screen.findByText(/DuckDB 1\.4 is deprecated \(upstream support ended 2026-11-17\)/),
      ).toBeInTheDocument()
    })

    it('offers no restart for an agent on a retired runtime', async () => {
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
            access_tier: 'operate',
            runtime: { id: '1.3', display_name: 'DuckDB 1.3', status: 'retired', state: 'pending' },
          }),
        ),
      )
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: ELASTIC })
      await user.click(await screen.findByRole('tab', { name: /overview/i }))

      await screen.findByText('Elastic compute')
      expect(screen.queryByRole('button', { name: /restart agent/i })).not.toBeInTheDocument()
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
  it('opens the agent audit across all users, not just the viewer', async () => {
    // History defaults to the caller's own runs, so a bare `?agent=` link shows
    // an admin only the queries they personally ran on this agent — usually
    // none. The audit link has to opt out of that default explicitly.
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: ELASTIC })
    // The audit link lives on Overview; the page opens on Monitoring.
    await user.click(await screen.findByRole('tab', { name: /overview/i }))

    await user.click(
      await screen.findByRole('button', { name: /view audit/i }),
    )

    expect(router.state.location.pathname).toContain('/history')
    expect(router.state.location.search).toEqual({ agent: 'ag-5', user: 'all' })
  })
})
