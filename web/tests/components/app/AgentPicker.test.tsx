import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { createWrapper } from '@tests/utils'
import { AgentPicker } from '@/components/app/AgentPicker'

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }))

const CAPS = {
  duckdb_version: '1.5.4',
  extensions: ['httpfs'],
  memory_limit_gb: 8,
  cores: 2,
  cpu_model: null,
  cpu_cores_physical: null,
  tailscale_ip: null,
  host: 'aci',
}

const RUNNING = {
  id: 'ag-run',
  name: 'warehouse-a',
  status: 'healthy',
  capabilities: CAPS,
  last_ping_at: new Date().toISOString(),
  created_at: '2026-06-01T00:00:00Z',
  provider: 'azure_aci',
  lifecycle: 'running',
  requested_cpu: 2,
  requested_memory_gb: 8,
  hourly_cost: 0.14,
  // The server resolves the caller's tier per request and ships it on the agent;
  // the lifecycle controls below are gated on it, not on a global role.
  access_tier: 'operate',
  access_mode: 'open',
}
const TERMINATED = {
  ...RUNNING,
  id: 'ag-term',
  name: 'gone-b',
  status: 'unavailable',
  capabilities: null,
  lifecycle: 'terminated',
}
const STATIC_DOWN = {
  ...RUNNING,
  id: 'ag-static',
  name: 'static-c',
  status: 'unavailable',
  provider: null,
  lifecycle: null,
  access_tier: 'use',
}

function renderPicker(props: Partial<React.ComponentProps<typeof AgentPicker>> = {}) {
  const { wrapper: Wrapper } = createWrapper()
  const onChange = vi.fn()
  render(
    <Wrapper>
      <AgentPicker value={null} onChange={onChange} {...props} />
    </Wrapper>,
  )
  return { onChange }
}

async function openPicker() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('combobox', { name: 'Compute agent' }))
  return user
}

describe('AgentPicker search and grouping', () => {
  it('finds an agent by name, not only by id', async () => {
    // Regression: the search matched the agent's UUID, so typing its name
    // found nothing.
    server.use(
      http.get('/api/agents', () =>
        HttpResponse.json([
          { ...RUNNING, id: '1aa40d14-7a2e', name: 'ducklake-bench', provider: null, lifecycle: null },
          { ...RUNNING, id: '9f0c2b1d-1111', name: 'tpch-duckdb155', provider: null, lifecycle: null },
        ]),
      ),
    )
    renderPicker()
    const user = await openPicker()

    await user.type(screen.getByPlaceholderText('Search agents…'), 'ducklake')

    expect(await screen.findByText('ducklake-bench')).toBeInTheDocument()
    expect(screen.queryByText('tpch-duckdb155')).not.toBeInTheDocument()
  })

  it('lists running agents first and folds the unavailable ones away', async () => {
    server.use(http.get('/api/agents', () => HttpResponse.json([STATIC_DOWN, RUNNING])))
    renderPicker()
    const user = await openPicker()

    expect(await screen.findByText('Running')).toBeInTheDocument()
    expect(screen.getByText('warehouse-a')).toBeInTheDocument()
    expect(screen.queryByText('static-c')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Show 1 unavailable' }))
    expect(screen.getByText('static-c')).toBeInTheDocument()
  })

  it('still finds an unavailable agent when searching for it', async () => {
    server.use(http.get('/api/agents', () => HttpResponse.json([STATIC_DOWN, RUNNING])))
    renderPicker()
    const user = await openPicker()

    await user.type(screen.getByPlaceholderText('Search agents…'), 'static')

    expect(await screen.findByText('static-c')).toBeInTheDocument()
  })

  it('tells same-named agents apart', async () => {
    server.use(
      http.get('/api/agents', () =>
        HttpResponse.json([
          { ...RUNNING, id: 'a1', name: 'tpch', capabilities: { ...CAPS, host: 'host-one' } },
          { ...RUNNING, id: 'a2', name: 'tpch', capabilities: { ...CAPS, host: 'host-two' } },
        ]),
      ),
    )
    renderPicker()
    await openPicker()

    expect(await screen.findByText('host-one')).toBeInTheDocument()
    expect(screen.getByText('host-two')).toBeInTheDocument()
  })

  it('names the trigger for assistive tech', async () => {
    server.use(http.get('/api/agents', () => HttpResponse.json([RUNNING])))
    renderPicker({ value: 'ag-run' })
    const trigger = await screen.findByRole('combobox', { name: 'Compute agent' })
    await waitFor(() => expect(trigger).toHaveTextContent('warehouse-a'))
  })
})

describe('AgentPicker lifecycle controls', () => {
  it('asks before stopping a running elastic agent', async () => {
    let terminated = false
    server.use(
      http.get('/api/agents', () => HttpResponse.json([RUNNING])),
      http.post('/api/admin/agents/ag-run/terminate', () => {
        terminated = true
        return HttpResponse.json({ ...RUNNING, lifecycle: 'terminated' }, { status: 202 })
      }),
    )
    renderPicker()
    const user = await openPicker()

    await user.click(await screen.findByRole('button', { name: 'Actions for warehouse-a' }))
    await user.click(await screen.findByRole('menuitem', { name: /stop/i }))
    expect(terminated).toBe(false)

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Stop warehouse-a?')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Stop agent' }))
    await waitFor(() => expect(terminated).toBe(true))
  })

  it('starts a stopped elastic agent from its menu', async () => {
    let restarted = false
    server.use(
      http.get('/api/agents', () => HttpResponse.json([TERMINATED])),
      http.post('/api/admin/agents/ag-term/restart', () => {
        restarted = true
        return HttpResponse.json({ ...TERMINATED, lifecycle: 'provisioning' }, { status: 202 })
      }),
    )
    renderPicker()
    const user = await openPicker()

    await user.click(await screen.findByRole('button', { name: 'Actions for gone-b' }))
    await user.click(await screen.findByRole('menuitem', { name: /start/i }))
    await waitFor(() => expect(restarted).toBe(true))
  })

  it('hides the lifecycle controls from a use-tier user', async () => {
    server.use(http.get('/api/agents', () => HttpResponse.json([{ ...RUNNING, access_tier: 'use' }])))
    renderPicker()
    await openPicker()

    // The agent is still selectable — `use` is exactly the tier that targets it.
    expect(await screen.findByText('warehouse-a')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /actions for warehouse-a/i })).not.toBeInTheDocument()
  })
})

describe('AgentPicker stopped elastic agents', () => {
  beforeEach(() => {
    server.use(http.get('/api/agents', () => HttpResponse.json([TERMINATED])))
  })

  it('lets a caller that allows it pick a stopped elastic agent, and says it starts on run', async () => {
    const { onChange } = renderPicker({ allowTerminatedElastic: true })
    const user = await openPicker()

    expect(await screen.findByText(/starts on run/i)).toBeInTheDocument()
    await user.click(screen.getByText('gone-b'))

    expect(onChange).toHaveBeenCalledWith('ag-term')
  })

  it('does not let other callers pick one', async () => {
    const { onChange } = renderPicker()
    const user = await openPicker()

    await user.click(await screen.findByText('gone-b'))

    expect(onChange).not.toHaveBeenCalled()
  })

  it('never lets an offline static agent be picked', async () => {
    server.use(http.get('/api/agents', () => HttpResponse.json([STATIC_DOWN])))
    const { onChange } = renderPicker({ allowTerminatedElastic: true })
    const user = await openPicker()

    await user.click(screen.getByRole('button', { name: 'Show 1 unavailable' }))
    await user.click(await screen.findByText('static-c'))

    // Nothing can start an operator-run host, so the run would only fail.
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('AgentPicker catalog-kind compatibility', () => {
  it('flags and refuses an agent that cannot serve a DuckLake catalog', async () => {
    server.use(http.get('/api/agents', () => HttpResponse.json([RUNNING])))
    const { onChange } = renderPicker({ workspaceCatalogKinds: ['ducklake'] })
    const user = await openPicker()

    expect(await screen.findByText(/Missing extension for ducklake catalogs/i)).toBeInTheDocument()
    await user.click(screen.getByText('warehouse-a'))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('does not flag an agent that has the DuckLake extensions', async () => {
    const capable = {
      ...RUNNING,
      capabilities: { ...CAPS, extensions: ['httpfs', 'ducklake', 'postgres_scanner'] },
    }
    server.use(http.get('/api/agents', () => HttpResponse.json([capable])))
    renderPicker({ workspaceCatalogKinds: ['ducklake'] })
    await openPicker()

    await screen.findByText('warehouse-a')
    expect(screen.queryByText(/Missing extension for/i)).not.toBeInTheDocument()
  })

  it('never flags an Iceberg catalog, which has never been gated', async () => {
    server.use(http.get('/api/agents', () => HttpResponse.json([RUNNING])))
    renderPicker({ workspaceCatalogKinds: ['iceberg_polaris'] })
    await openPicker()

    await screen.findByText('warehouse-a')
    expect(screen.queryByText(/Missing extension for/i)).not.toBeInTheDocument()
  })
})
