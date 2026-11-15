import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'
import { CURRENT_USER } from '@/mock/fixtures/users'

describe('workspace route guard', () => {
  it('redirects to /login when deep-linking a protected route while logged out', async () => {
    server.use(
      http.get('/api/me', () => HttpResponse.json({ error: 'unauthorized' }, { status: 401 })),
    )

    renderWithProviders({ initialRoute: '/acme-analytics/admin/users' })

    // Lands on the login form instead of a blank app shell.
    expect(await screen.findByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('opens the deep link again after signing in', async () => {
    let signedIn = false
    server.use(
      http.get('/api/me', () =>
        signedIn
          ? HttpResponse.json(CURRENT_USER)
          : HttpResponse.json({ error: 'unauthorized' }, { status: 401 }),
      ),
      http.post('/api/auth/login', () => {
        signedIn = true
        return HttpResponse.json(CURRENT_USER)
      }),
    )
    const link = '/acme-analytics/admin/users?tab=invites#pending'
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: link })

    await user.type(await screen.findByLabelText(/email/i), 'test@example.com')
    expect(router.state.location.pathname).toBe('/login')
    expect(router.state.location.search).toEqual({ redirect: link })
    await user.type(screen.getByLabelText(/password/i), 'password')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(router.state.location.href).toBe(link))
  })

  it('shows a not-found page for an unknown workspace slug', async () => {
    // /api/me succeeds (default handler); /api/workspaces/:ws 404s for unknown slugs.
    renderWithProviders({ initialRoute: '/this-route-does-not-exist' })

    expect(await screen.findByText(/page not found/i)).toBeInTheDocument()
  })
})
