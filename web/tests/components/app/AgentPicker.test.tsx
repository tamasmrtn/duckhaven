import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { createWrapper } from '@tests/utils'
import { AgentPicker } from '@/components/app/AgentPicker'

function renderPicker() {
  const { wrapper: Wrapper } = createWrapper()
  return render(
    <Wrapper>
      <AgentPicker value={null} onChange={vi.fn()} />
    </Wrapper>,
  )
}

const RUNNING = {
  id: 'ag-run',
  name: 'warehouse-a',
  status: 'healthy',
  capabilities: { duckdb_version: '1.5.4', extensions: ['httpfs'], memory_limit_gb: 8, cores: 2, cpu_model: null, cpu_cores_physical: null, tailscale_ip: null, host: 'aci' },
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
const TERMINATED = { ...RUNNING, id: 'ag-term', name: 'gone-b', status: 'unavailable', capabilities: null, lifecycle: 'terminated' }

describe('AgentPicker elastic controls (worksheets)', () => {
  it('lets an operate-tier user terminate a running elastic agent from the picker', async () => {
    let terminated = false
    server.use(
      http.get('/api/agents', () => HttpResponse.json([RUNNING])),
      http.post('/api/admin/agents/ag-run/terminate', () => {
        terminated = true
        return HttpResponse.json({ ...RUNNING, lifecycle: 'terminated' }, { status: 202 })
      }),
    )
    const user = userEvent.setup()
    renderPicker()

    await user.click(await screen.findByRole('combobox'))
    await user.click(await screen.findByRole('button', { name: /terminate warehouse-a/i }))
    await waitFor(() => expect(terminated).toBe(true))
  })

  it('lets an operate-tier user restart a terminated elastic agent from the picker', async () => {
    let restarted = false
    server.use(
      http.get('/api/agents', () => HttpResponse.json([TERMINATED])),
      http.post('/api/admin/agents/ag-term/restart', () => {
        restarted = true
        return HttpResponse.json({ ...TERMINATED, lifecycle: 'provisioning' }, { status: 202 })
      }),
    )
    const user = userEvent.setup()
    renderPicker()

    await user.click(await screen.findByRole('combobox'))
    await user.click(await screen.findByRole('button', { name: /restart gone-b/i }))
    await waitFor(() => expect(restarted).toBe(true))
  })

  it('hides the lifecycle controls from a use-tier user', async () => {
    server.use(
      http.get('/api/agents', () => HttpResponse.json([{ ...RUNNING, access_tier: 'use' }])),
    )
    const user = userEvent.setup()
    renderPicker()

    await user.click(await screen.findByRole('combobox'))
    // The agent is still selectable — `use` is exactly the tier that targets it.
    expect(await screen.findByText('warehouse-a')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /terminate warehouse-a/i })).not.toBeInTheDocument()
  })

  it('offers only the agents the server returned', async () => {
    // A restricted agent the caller has no tier on is filtered out server-side,
    // so there is no client rule to get wrong.
    server.use(http.get('/api/agents', () => HttpResponse.json([RUNNING])))
    const user = userEvent.setup()
    renderPicker()

    await user.click(await screen.findByRole('combobox'))
    expect(await screen.findByText('warehouse-a')).toBeInTheDocument()
    expect(screen.queryByText('gone-b')).not.toBeInTheDocument()
  })
})

describe('AgentPicker terminated elastic agents', () => {
  function renderFor(allowTerminatedElastic: boolean) {
    const { wrapper: Wrapper } = createWrapper()
    return render(
      <Wrapper>
        <AgentPicker
          value={null}
          onChange={onChange}
          allowTerminatedElastic={allowTerminatedElastic}
        />
      </Wrapper>,
    )
  }
  let onChange = vi.fn()
  beforeEach(() => {
    onChange = vi.fn()
    server.use(http.get('/api/agents', () => HttpResponse.json([TERMINATED])))
  })

  it('lets a schedule pick a stopped elastic agent, and says why', async () => {
    const user = userEvent.setup()
    renderFor(true)

    await user.click(await screen.findByRole('combobox'))
    expect(await screen.findByText(/will be started for each run/i)).toBeInTheDocument()
    await user.click(screen.getByText('gone-b'))

    // The scheduler restarts it at run time, so choosing it is the point.
    expect(onChange).toHaveBeenCalledWith('ag-term')
  })

  it('does not let a worksheet pick one — that dispatch happens now', async () => {
    const user = userEvent.setup()
    renderFor(false)

    await user.click(await screen.findByRole('combobox'))
    await user.click(await screen.findByText('gone-b'))

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.queryByText(/will be started for each run/i)).not.toBeInTheDocument()
  })

  it('never lets an offline static agent be picked, in either context', async () => {
    const STATIC_DOWN = {
      ...TERMINATED,
      id: 'ag-static',
      name: 'static-c',
      provider: null,
      lifecycle: null,
    }
    server.use(http.get('/api/agents', () => HttpResponse.json([STATIC_DOWN])))
    const user = userEvent.setup()
    renderFor(true)

    await user.click(await screen.findByRole('combobox'))
    await user.click(await screen.findByText('static-c'))

    // Nothing can start an operator-run host, so the run would only fail.
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('AgentPicker catalog-kind compatibility', () => {
  function renderWithKinds(kinds: ('iceberg_polaris' | 'ducklake')[]) {
    const { wrapper: Wrapper } = createWrapper()
    return render(
      <Wrapper>
        <AgentPicker value={null} onChange={vi.fn()} workspaceCatalogKinds={kinds} />
      </Wrapper>,
    )
  }

  it('flags an agent that cannot serve a DuckLake catalog', async () => {
    // Dispatch checks two axes; the picker mirrored only the storage one, so an
    // agent without the ducklake extension looked selectable and the query
    // failed at dispatch instead of the agent being greyed out.
    server.use(http.get('/api/agents', () => HttpResponse.json([RUNNING])))
    renderWithKinds(['ducklake'])

    await userEvent.click(await screen.findByRole('combobox'))
    expect(await screen.findByText(/Missing extension for ducklake catalogs/i)).toBeInTheDocument()
    expect(screen.getByText(/ducklake/i)).toBeInTheDocument()
  })

  it('does not flag an agent that has the DuckLake extensions', async () => {
    const capable = {
      ...RUNNING,
      capabilities: {
        ...RUNNING.capabilities,
        extensions: ['httpfs', 'ducklake', 'postgres_scanner'],
      },
    }
    server.use(http.get('/api/agents', () => HttpResponse.json([capable])))
    renderWithKinds(['ducklake'])

    await userEvent.click(await screen.findByRole('combobox'))
    await screen.findByText('warehouse-a')
    expect(screen.queryByText(/Missing extension for/i)).not.toBeInTheDocument()
  })

  it('never flags an Iceberg catalog, which has never been gated', async () => {
    // Matches the server: _CATALOG_KIND_EXTENSIONS["iceberg_polaris"] is empty,
    // so enforcing it client-side would grey out agents the API accepts.
    server.use(http.get('/api/agents', () => HttpResponse.json([RUNNING])))
    renderWithKinds(['iceberg_polaris'])

    await userEvent.click(await screen.findByRole('combobox'))
    await screen.findByText('warehouse-a')
    expect(screen.queryByText(/Missing extension for/i)).not.toBeInTheDocument()
  })
})
