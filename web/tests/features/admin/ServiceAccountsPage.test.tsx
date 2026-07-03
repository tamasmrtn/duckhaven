import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'
import { SERVICE_ACCOUNTS } from '@/mock/fixtures/service-accounts'

const ROUTE = '/acme-analytics/admin/service-accounts'

async function openTokenManager(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: /actions for ci-runner/i }))
  await user.click(await screen.findByRole('menuitem', { name: /manage tokens/i }))
}

describe('ServiceAccountsPage', () => {
  it('renders the service-account list and count', async () => {
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByText('ci-runner')
    for (const sa of SERVICE_ACCOUNTS) {
      expect(screen.getByText(sa.name)).toBeInTheDocument()
    }
    expect(
      screen.getByText(`${SERVICE_ACCOUNTS.length} service accounts`),
    ).toBeInTheDocument()
  })

  it('shows an empty state when there are no service accounts', async () => {
    server.use(
      http.get('/api/admin/service-accounts', () => HttpResponse.json([])),
    )
    renderWithProviders({ initialRoute: ROUTE })
    expect(await screen.findByText(/no service accounts yet/i)).toBeInTheDocument()
  })

  it('creates a service account', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByText('ci-runner')

    await user.click(screen.getByRole('button', { name: /new service account/i }))
    await user.type(screen.getByLabelText(/name/i), 'release-bot')
    await user.click(screen.getByRole('button', { name: /create service account/i }))

    expect(await screen.findByText('release-bot')).toBeInTheDocument()
  })

  it('issues a PAT and reveals the secret exactly once', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })
    await openTokenManager(user)

    await user.click(await screen.findByRole('button', { name: /issue token/i }))

    const secret = await screen.findByTestId('pat-secret')
    expect(secret.textContent).toMatch(/dh_pat_/)
    expect(screen.getByText(/only time this token will be shown/i)).toBeInTheDocument()
  })

  it('clears the revealed secret when the dialog is closed and reopened', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })
    await openTokenManager(user)

    await user.click(await screen.findByRole('button', { name: /issue token/i }))
    await screen.findByTestId('pat-secret')
    await user.click(screen.getByRole('button', { name: /^done$/i }))

    await openTokenManager(user)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /issue token/i })).toBeInTheDocument()
      expect(screen.queryByTestId('pat-secret')).not.toBeInTheDocument()
    })
  })
})
