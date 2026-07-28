import { describe, it, expect, vi } from 'vitest'
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
}
const TERMINATED = { ...RUNNING, id: 'ag-term', name: 'gone-b', status: 'unavailable', capabilities: null, lifecycle: 'terminated' }

describe('AgentPicker elastic controls (worksheets)', () => {
  it('lets an admin terminate a running elastic agent from the picker', async () => {
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

  it('lets an admin restart a terminated elastic agent from the picker', async () => {
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
})
