import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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
      expect(screen.getByText('Generate bootstrap token')).toBeInTheDocument()
    })
  })

  it('displays a token after clicking Generate token', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))
    await user.click(await screen.findByRole('button', { name: /generate token/i }))

    await waitFor(() => {
      expect(screen.getByText(/dh_boot_/)).toBeInTheDocument()
      expect(screen.getByLabelText('Copy token')).toBeInTheDocument()
    })
  })

  it('clears the token when the modal is closed and reopened', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AGENTS_ROUTE })

    // Open → generate → close
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))
    await user.click(await screen.findByRole('button', { name: /generate token/i }))
    await screen.findByText(/dh_boot_/)
    await user.click(screen.getByRole('button', { name: /^done$/i }))

    // Reopen — should show the generate button, not the token
    await user.click(await screen.findByRole('button', { name: /generate bootstrap/i }))

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /generate token/i })).toBeInTheDocument()
      expect(screen.queryByText(/dh_boot_/)).not.toBeInTheDocument()
    })
  })
})
