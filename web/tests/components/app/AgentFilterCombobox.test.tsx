import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { createWrapper } from '@tests/utils'
import { AgentFilterCombobox } from '@/components/app/AgentFilterCombobox'

const AGENTS = [
  { id: 'ag-a', name: 'warehouse-a', status: 'healthy', capabilities: null, last_ping_at: null, created_at: '2026-01-01T00:00:00Z' },
  { id: 'ag-b', name: 'warehouse-b', status: 'healthy', capabilities: null, last_ping_at: null, created_at: '2026-01-01T00:00:00Z' },
]

function renderCombobox(value: string | null, onChange = vi.fn()) {
  server.use(http.get('/api/agents', () => HttpResponse.json(AGENTS)))
  const { wrapper: Wrapper } = createWrapper()
  return {
    onChange,
    ...render(
      <Wrapper>
        <AgentFilterCombobox value={value} onChange={onChange} />
      </Wrapper>,
    ),
  }
}

describe('AgentFilterCombobox', () => {
  it('defaults to "All agents" and lists every agent when opened', async () => {
    const user = userEvent.setup()
    renderCombobox(null)

    expect(screen.getByRole('combobox')).toHaveTextContent('All agents')
    await user.click(screen.getByRole('combobox'))

    expect(await screen.findByText('warehouse-a')).toBeInTheDocument()
    expect(screen.getByText('warehouse-b')).toBeInTheDocument()
  })

  it('selects an agent and reports its id', async () => {
    const user = userEvent.setup()
    const { onChange } = renderCombobox(null)

    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByText('warehouse-b'))

    expect(onChange).toHaveBeenCalledWith('ag-b')
  })

  it('shows the selected agent name on the trigger', async () => {
    renderCombobox('ag-a')
    await waitFor(() =>
      expect(screen.getByRole('combobox')).toHaveTextContent('warehouse-a'),
    )
  })

  it('clears the filter via "All agents"', async () => {
    const user = userEvent.setup()
    const { onChange } = renderCombobox('ag-a')

    await user.click(screen.getByRole('combobox'))
    await user.click(await screen.findByText('All agents'))

    expect(onChange).toHaveBeenCalledWith(null)
  })
})
