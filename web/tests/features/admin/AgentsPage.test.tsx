import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'
import { AGENTS } from '@/mock/fixtures/agents'

const AGENTS_ROUTE = '/acme-analytics/admin/agents'

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

  it('shows an empty state when there are no agents (Bug #10)', async () => {
    server.use(
      http.get('/api/admin/agents', () => HttpResponse.json([])),
    )
    renderWithProviders({ initialRoute: AGENTS_ROUTE })

    expect(await screen.findByText(/no agents connected/i)).toBeInTheDocument()
  })

  it('opens the agent drawer when a row is clicked', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await screen.findByText('agent-b')

    await user.click(screen.getByText('agent-b'))

    // Sheet becomes visible with the agent name in the header
    await waitFor(() => {
      expect(screen.getAllByText('agent-b').length).toBeGreaterThan(1)
    })
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
    expect(screen.getByLabelText('Copy compose snippet')).toBeInTheDocument()
  })

  it('renders the snippet with a readable (theme-independent) code text color', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))
    await user.click(await screen.findByRole('button', { name: /generate snippet/i }))

    const snippet = await screen.findByTestId('agent-compose-snippet')
    // The dark code background must pair with the light --text-code token,
    // not --text-primary (which is dark in light mode — Bug #2).
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
})
