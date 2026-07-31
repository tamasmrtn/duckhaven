import { describe, it, expect } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'
import { AGENTS } from '@/mock/fixtures/agents'

const AGENTS_ROUTE = '/acme-analytics/compute'

describe('AgentsPage', () => {
  it('renders the full agent list', async () => {
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    // Wait for the list to appear (MSW returns agents)
    await screen.findByText('agent-a')
    for (const agent of AGENTS) {
      expect(screen.getByText(agent.name)).toBeInTheDocument()
    }
  })

  it('shows the correct agent count', async () => {
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await screen.findByText(`${AGENTS.length} agents`)
  })

  it('exposes each agent status via an accessible label, not color alone', async () => {
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await screen.findByText('agent-a')
    // The status indicator dots carry their textual state for assistive tech.
    expect(screen.getAllByRole('img', { name: 'healthy' }).length).toBeGreaterThan(0)
    expect(screen.getByRole('img', { name: 'unavailable' })).toBeInTheDocument()
  })

  it('shows an empty state when there are no agents', async () => {
    server.use(
      http.get('/api/admin/agents', () => HttpResponse.json([])),
    )
    renderWithProviders({ initialRoute: AGENTS_ROUTE })

    expect(await screen.findByText(/no agents connected/i)).toBeInTheDocument()
  })

  it('navigates to the agent detail page when a row is clicked', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await screen.findByText('agent-b')

    await user.click(screen.getByText('agent-b'))

    // The detail route replaces the old drawer, so the view is linkable and the
    // charts get the full width.
    expect(
      await screen.findByRole('button', { name: /back to agents/i }),
    ).toBeInTheDocument()
    expect(await screen.findByRole('tab', { name: /monitoring/i })).toBeInTheDocument()
  })

  it('opens the bootstrap modal when the button is clicked', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    const btn = await screen.findByRole('button', { name: /generate bootstrap/i })

    await user.click(btn)

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /add an agent/i })).toBeInTheDocument()
    })
  })

  it('renders a copy-pasteable compose snippet after generating', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))
    await user.click(await screen.findByRole('button', { name: /generate snippet/i }))

    const snippet = await screen.findByTestId('agent-compose-snippet')
    expect(snippet.textContent).toMatch(/dh_boot_/)
    expect(snippet.textContent).toContain('CONTROL_PLANE_URL: ws://localhost:8000/agents/connect')
    expect(snippet.textContent).toContain('image: ghcr.io/tamasmrtn/duckhaven-agent:latest')
    expect(snippet.textContent).toContain('restart: unless-stopped')
    // The result-server bind host must be documented in the snippet so
    // externally-added agents are reachable by the control plane out of the box.
    expect(snippet.textContent).toContain('RESULTS_HTTP_HOST: 0.0.0.0')
    expect(screen.getByLabelText('Copy compose snippet')).toBeInTheDocument()
  })

  it('bakes a typed display name into the snippet as AGENT_NAME', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))
    await user.type(
      screen.getByLabelText(/display name/i),
      'analytics-prod-1',
    )
    await user.click(await screen.findByRole('button', { name: /generate snippet/i }))

    const snippet = await screen.findByTestId('agent-compose-snippet')
    expect(snippet.textContent).toContain('AGENT_NAME: analytics-prod-1')
  })

  it('omits AGENT_NAME when no display name is given', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))
    await user.click(await screen.findByRole('button', { name: /generate snippet/i }))

    const snippet = await screen.findByTestId('agent-compose-snippet')
    expect(snippet.textContent).not.toContain('AGENT_NAME')
  })

  it('renders the snippet with a readable (theme-independent) code text color', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))
    await user.click(await screen.findByRole('button', { name: /generate snippet/i }))

    const snippet = await screen.findByTestId('agent-compose-snippet')
    // The dark code background must pair with the light --text-code token,
    // not --text-primary (which is dark in light mode).
    expect(snippet.className).toContain('text-[var(--text-code)]')
    expect(snippet.className).not.toContain('text-text-primary')
  })

  it('clears the snippet when the modal is closed and reopened', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })

    // Open → generate → close
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))
    await user.click(await screen.findByRole('button', { name: /generate snippet/i }))
    await screen.findByTestId('agent-compose-snippet')
    await user.click(screen.getByRole('button', { name: /^done$/i }))

    // Reopen — should show the generate button, not the snippet
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /generate snippet/i })).toBeInTheDocument()
      expect(screen.queryByTestId('agent-compose-snippet')).not.toBeInTheDocument()
    })
  })

  describe('elastic compute', () => {
    it('shows the hourly cost of an elastic agent in the table', async () => {
      renderWithProviders({ initialRoute: AGENTS_ROUTE })
      await screen.findByText('warehouse-a')
      // ag-5 is a 4 vCPU / 16 GB elastic agent → $0.28/hr.
      expect(screen.getByText('$0.28/hr')).toBeInTheDocument()
    })

    it('opens the New compute dialog with vCPU + memory sliders and a live cost', async () => {
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: AGENTS_ROUTE })

      await user.click(await screen.findByRole('button', { name: /new compute/i }))
      expect(
        await screen.findByRole('heading', { name: /new compute/i }),
      ).toBeInTheDocument()

      // Two range sliders and a cost summary (default 1 vCPU / 1 GB → $0.05/hr).
      expect(await screen.findByRole('slider', { name: /vcpu/i })).toBeInTheDocument()
      expect(screen.getByRole('slider', { name: /memory/i })).toBeInTheDocument()
      const summary = await screen.findByTestId('compute-cost-summary')
      expect(summary).toHaveTextContent('1 vCPU · 1 GB')
    })

    it('updates the cost as the sliders change', async () => {
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: AGENTS_ROUTE })
      await user.click(await screen.findByRole('button', { name: /new compute/i }))

      const cpu = await screen.findByRole('slider', { name: /vcpu/i })
      const mem = screen.getByRole('slider', { name: /memory/i })
      // 4 vCPU * 0.0486 + 16 GB * 0.0054 = 0.2808 → $0.28/hr
      fireEvent.change(cpu, { target: { value: '4' } })
      fireEvent.change(mem, { target: { value: '16' } })

      const summary = screen.getByTestId('compute-cost-summary')
      expect(summary).toHaveTextContent('4 vCPU · 16 GB')
      expect(summary).toHaveTextContent('$0.28/hr')
    })

    it('provisions an agent at the chosen cpu/memory/idle and adds it to the list', async () => {
      let posted: Record<string, unknown> | null = null
      server.use(
        http.post('/api/admin/agents/elastic', async ({ request }) => {
          posted = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ id: 'ag-new', name: 'analytics-warehouse' }, { status: 202 })
        }),
        http.get('/api/admin/agents', () =>
          HttpResponse.json([
            ...AGENTS,
            {
              id: 'ag-new',
              name: 'analytics-warehouse',
              status: 'unavailable',
              capabilities: null,
              last_ping_at: null,
              created_at: new Date().toISOString(),
              provider: 'azure_aci',
              lifecycle: 'provisioning',
              requested_cpu: 2,
              requested_memory_gb: 8,
              hourly_cost: 0.14,
              idle_timeout_minutes: 20,
            },
          ]),
        ),
      )
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: AGENTS_ROUTE })

      await user.click(await screen.findByRole('button', { name: /new compute/i }))
      fireEvent.change(await screen.findByRole('slider', { name: /vcpu/i }), {
        target: { value: '2' },
      })
      fireEvent.change(screen.getByRole('slider', { name: /memory/i }), {
        target: { value: '8' },
      })
      fireEvent.change(screen.getByLabelText(/auto-terminate after idle/i), {
        target: { value: '20' },
      })
      await user.type(screen.getByLabelText(/^name/i), 'analytics-warehouse')
      await user.click(screen.getByRole('button', { name: /create compute/i }))

      await waitFor(() =>
        expect(posted).toEqual({
          cpu: 2,
          memory_gb: 8,
          idle_timeout_minutes: 20,
          name: 'analytics-warehouse',
          // Sent on every create; `open` is the default and matches how every
          // agent behaved before per-agent access existed.
          access_mode: 'open',
        }),
      )
      expect(await screen.findByText('analytics-warehouse')).toBeInTheDocument()
    })

    it('creates an agent restricted so it is never briefly usable by everyone', async () => {
      let posted: Record<string, unknown> | null = null
      server.use(
        http.post('/api/admin/agents/elastic', async ({ request }) => {
          posted = (await request.json()) as Record<string, unknown>
          return HttpResponse.json({ id: 'ag-new' }, { status: 202 })
        }),
      )
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: AGENTS_ROUTE })

      await user.click(await screen.findByRole('button', { name: /new compute/i }))
      await user.click(await screen.findByRole('combobox', { name: /who can use it/i }))
      await user.click(await screen.findByRole('option', { name: /only people i grant/i }))
      await user.click(screen.getByRole('button', { name: /create compute/i }))

      await waitFor(() =>
        expect(posted).toMatchObject({ access_mode: 'restricted' }),
      )
    })

    it('explains what each access mode means before you commit', async () => {
      const user = userEvent.setup()
      renderWithProviders({ initialRoute: AGENTS_ROUTE })

      await user.click(await screen.findByRole('button', { name: /new compute/i }))
      expect(await screen.findByText(/any signed-in user can run work/i)).toBeInTheDocument()

      await user.click(await screen.findByRole('combobox', { name: /who can use it/i }))
      await user.click(await screen.findByRole('option', { name: /only people i grant/i }))

      expect(
        await screen.findByText(/nobody else sees this agent until you grant access/i),
      ).toBeInTheDocument()
    })
  })

  it('renders cost in the currency the provider quotes, not a hardcoded $', async () => {
    // The rates an operator enters are copied from their provider's pricing page,
    // which quotes them in a currency. Assuming USD puts the wrong symbol on every
    // agent in the table while the create dialog shows the right one.
    server.use(
      http.get('/api/admin/agents/compute-options', () =>
        HttpResponse.json({
          enabled: true,
          provider: 'azure_aci',
          currency: 'EUR',
          cpu_min: 1,
          cpu_max: 4,
          cpu_step: 1,
          memory_min_gb: 1,
          memory_max_gb: 16,
          memory_step_gb: 1,
          price_vcpu_hour: 0.05,
          price_memory_gb_hour: 0.005,
          default_idle_minutes: 15,
        }),
      ),
      http.get('/api/admin/agents', () =>
        HttpResponse.json([
          {
            id: 'ag-eur',
            name: 'euro-agent',
            status: 'healthy',
            capabilities: null,
            last_ping_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            provider: 'azure_aci',
            lifecycle: 'running',
            requested_cpu: 2,
            requested_memory_gb: 8,
            hourly_cost: 0.14,
            idle_timeout_minutes: 20,
          },
        ]),
      ),
    )
    renderWithProviders({ initialRoute: AGENTS_ROUTE })

    await screen.findByText('euro-agent')
    expect(screen.queryByText(/\$0\.14/)).not.toBeInTheDocument()
    expect(screen.getAllByText(/EUR\s*0\.14/).length).toBeGreaterThan(0)
  })

  it('shows no cost when the provider prices nothing', async () => {
    // A container on hardware you already own is not priced by anyone, so there is
    // no currency and no figure to render.
    server.use(
      http.get('/api/admin/agents/compute-options', () =>
        HttpResponse.json({
          enabled: true,
          provider: 'docker',
          currency: null,
          cpu_min: 1,
          cpu_max: 7,
          cpu_step: 1,
          memory_min_gb: 1,
          memory_max_gb: 28,
          memory_step_gb: 1,
          price_vcpu_hour: 0,
          price_memory_gb_hour: 0,
          default_idle_minutes: 15,
        }),
      ),
      http.get('/api/admin/agents', () =>
        HttpResponse.json([
          {
            id: 'ag-local',
            name: 'local-agent',
            status: 'healthy',
            capabilities: null,
            last_ping_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
            provider: 'docker',
            lifecycle: 'running',
            requested_cpu: 2,
            requested_memory_gb: 4,
            hourly_cost: 0,
            idle_timeout_minutes: 20,
          },
        ]),
      ),
    )
    renderWithProviders({ initialRoute: AGENTS_ROUTE })

    await screen.findByText('local-agent')
    expect(screen.queryByText(/\/hr/)).not.toBeInTheDocument()
  })

  it('clearing the idle-timeout field falls back to the default, not zero', async () => {
    // Number('') is 0, and ?? only catches null/undefined — so emptying the box to
    // retype posted idle_timeout_minutes: 0, which the schema rejects (ge=1) with a
    // 422 mid-edit.
    let posted: Record<string, unknown> | null = null
    server.use(
      http.post('/api/admin/agents/elastic', async ({ request }) => {
        posted = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ id: 'ag-x', name: 'x' }, { status: 202 })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })

    await user.click(await screen.findByRole('button', { name: /new compute/i }))
    fireEvent.change(await screen.findByLabelText(/auto-terminate after idle/i), {
      target: { value: '' },
    })
    await user.click(screen.getByRole('button', { name: /create compute/i }))

    await waitFor(() => expect(posted).not.toBeNull())
    expect(posted!.idle_timeout_minutes).not.toBe(0)
  })
})
