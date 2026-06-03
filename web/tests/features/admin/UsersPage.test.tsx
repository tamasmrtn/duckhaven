import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen } from '@testing-library/react'
import { renderWithProviders } from '@tests/utils'
import { server } from '@tests/mock/server'

const USERS_ROUTE = '/acme-analytics/admin/users'

describe('UsersPage', () => {
  it('renders the real users returned by the API, not static fixtures', async () => {
    // Override with a distinct user so the test fails if the page ever falls
    // back to hardcoded fixture data.
    server.use(
      http.get('/api/admin/users', () =>
        HttpResponse.json([
          {
            id: 'real-1',
            email: 'admin@admin.com',
            name: 'Real Admin',
            role: 'admin',
            theme: 'system',
            created_at: '2026-03-01T00:00:00Z',
          },
        ]),
      ),
    )

    renderWithProviders({ initialRoute: USERS_ROUTE })

    expect(await screen.findByText('admin@admin.com')).toBeInTheDocument()
    expect(screen.getByText('Real Admin')).toBeInTheDocument()
    expect(screen.getByText('1 users')).toBeInTheDocument()
    // The mock fixtures must not leak through.
    expect(screen.queryByText('marton@duckhaven.local')).not.toBeInTheDocument()
  })

  it('shows the empty state when there are no users', async () => {
    server.use(http.get('/api/admin/users', () => HttpResponse.json([])))
    renderWithProviders({ initialRoute: USERS_ROUTE })
    expect(await screen.findByText('No users yet.')).toBeInTheDocument()
  })
})
