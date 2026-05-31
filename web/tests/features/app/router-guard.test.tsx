import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'

describe('workspace route guard', () => {
  it('redirects to /login when deep-linking a protected route while logged out (Bug #3)', async () => {
    server.use(
      http.get('/api/me', () => HttpResponse.json({ error: 'unauthorized' }, { status: 401 })),
    )

    renderWithProviders({ initialRoute: '/acme-analytics/admin/users' })

    // Lands on the login form instead of a blank app shell.
    expect(await screen.findByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('shows a not-found page for an unknown workspace slug (Bug #5)', async () => {
    // /api/me succeeds (default handler); /api/workspaces/:ws 404s for unknown slugs.
    renderWithProviders({ initialRoute: '/this-route-does-not-exist' })

    expect(await screen.findByText(/page not found/i)).toBeInTheDocument()
  })
})
