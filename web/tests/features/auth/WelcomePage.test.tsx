import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { server } from '@tests/mock/server'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@tests/utils'
import { CURRENT_USER } from '@/mock/fixtures/users'

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

  it('tells someone without workspaces:create to ask an admin', async () => {
    server.use(
      http.get('/api/me', () =>
        HttpResponse.json({ ...CURRENT_USER, role: 'user', permissions: [] }),
      ),
    )
    renderWithProviders({ initialRoute: '/welcome' })

    expect(
      await screen.findByRole('heading', { name: /not in a workspace yet/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(CURRENT_USER.email)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /get started/i })).not.toBeInTheDocument()
  })

  it('lets someone without a workspace sign out, to try another account', async () => {
    // /welcome is outside the app shell: without this, the wrong account had
    // no way out but clearing cookies.
    let signedOut = false
    server.use(
      http.get('/api/me', () =>
        HttpResponse.json({ ...CURRENT_USER, role: 'user', permissions: [] }),
      ),
      http.post('/api/auth/logout', () => {
        signedOut = true
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: '/welcome' })

    await user.click(await screen.findByRole('button', { name: 'Sign out' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(signedOut).toBe(true)
  })
})
