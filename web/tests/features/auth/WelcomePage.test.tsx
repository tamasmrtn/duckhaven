import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { server } from '@tests/mock/server'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@tests/utils'

describe('WelcomePage', () => {
  it('opens the create-workspace dialog by default', async () => {
    renderWithProviders({ initialRoute: '/welcome' })
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(await screen.findByLabelText(/workspace name/i)).toBeInTheDocument()
  })

  it('creates a name-only workspace on a fresh deployment, then navigates', async () => {
    // Fresh deployment: no storage backends yet. The user only names the
    // workspace; the API auto-provisions bundled object storage.
    server.use(
      http.get('/api/admin/storage-backends', () => HttpResponse.json([])),
    )
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: '/welcome' })

    await user.type(await screen.findByLabelText(/workspace name/i), 'Analytics')
    expect(screen.queryByLabelText(/root uri/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /create workspace/i }))

    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/analytics/worksheets'),
    )
  })

  it('creates a workspace against an existing backend, then navigates', async () => {
    // Default handlers return existing backends; no inline backend form needed.
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: '/welcome' })

    await user.type(await screen.findByLabelText(/workspace name/i), 'Reports')
    expect(screen.queryByLabelText(/root uri/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /create workspace/i }))

    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/reports/worksheets'),
    )
  })
})
