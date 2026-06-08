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

  it('agent drawer has an accessible description', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await screen.findByText('agent-b')

    await user.click(screen.getByText('agent-b'))

    // SheetContent must wire aria-describedby to a description, otherwise Radix
    // logs a missing-description a11y warning when the drawer opens.
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-describedby')
    expect(
      screen.getByText('Agent status, capabilities, and credential management.'),
    ).toBeInTheDocument()
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
})
