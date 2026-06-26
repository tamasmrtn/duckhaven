import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@tests/utils'
import { server } from '@tests/mock/server'
import { ALL_USERS } from '@/mock/fixtures/users'

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

  it('creates a local user through the Add user dialog', async () => {
    let created: Record<string, unknown> | null = null
    server.use(
      http.post('/api/admin/users', async ({ request }) => {
        created = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ id: 'u-new', ...created }, { status: 201 })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: USERS_ROUTE })

    await user.click(await screen.findByRole('button', { name: /add user/i }))
    await user.type(screen.getByLabelText('Email'), 'new@duckhaven.local')
    await user.type(screen.getByLabelText('Name'), 'New Person')
    await user.type(screen.getByLabelText(/temporary password/i), 'pw-12345')
    await user.click(screen.getByRole('button', { name: /create user/i }))

    await waitFor(() => expect(created).not.toBeNull())
    expect(created).toMatchObject({
      email: 'new@duckhaven.local',
      name: 'New Person',
      role: 'user',
    })
  })

  it('deactivates a user from the row actions menu', async () => {
    let patch: Record<string, unknown> | null = null
    server.use(
      http.patch('/api/admin/users/:id', async ({ request }) => {
        patch = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({ ...ALL_USERS[1], ...patch })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: USERS_ROUTE })

    await user.click(await screen.findByRole('button', { name: /actions for jess/i }))
    await user.click(await screen.findByRole('menuitem', { name: /deactivate/i }))

    await waitFor(() => expect(patch).toEqual({ is_active: false }))
  })
})
