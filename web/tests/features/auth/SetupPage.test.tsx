import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { server } from '@tests/mock/server'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@tests/utils'

describe('SetupPage', () => {
  it('renders the setup-token, email and password fields', async () => {
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json({ needs_admin: true })),
    )
    renderWithProviders({ initialRoute: '/setup' })

    expect(await screen.findByLabelText(/setup token/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /create admin/i })).toBeInTheDocument()
  })

  it('shows the correct setup-token path in the hint (BUG-7)', async () => {
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json({ needs_admin: true })),
    )
    renderWithProviders({ initialRoute: '/setup' })

    expect(
      await screen.findByText(/cat \/var\/duckhaven\/setup_token/),
    ).toBeInTheDocument()
    expect(screen.queryByText(/secrets\/setup_token/)).not.toBeInTheDocument()
  })

  it('redirects to /login when setup is already complete', async () => {
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json({ needs_admin: false })),
    )
    const { router } = renderWithProviders({ initialRoute: '/setup' })
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
  })

  it('navigates to /welcome on successful admin creation', async () => {
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json({ needs_admin: true })),
    )
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: '/setup' })

    await user.type(await screen.findByLabelText(/setup token/i), 'tok-abc')
    await user.clear(screen.getByLabelText(/your name/i))
    await user.type(screen.getByLabelText(/your name/i), 'Tamas')
    await user.type(screen.getByLabelText(/email/i), 'admin@example.com')
    await user.type(screen.getByLabelText(/^password$/i), 'longenough')
    await user.click(screen.getByRole('button', { name: /create admin/i }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/welcome'))
  })

  it('shows a server error when the setup token is rejected', async () => {
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json({ needs_admin: true })),
      http.post('/api/setup/admin', () =>
        HttpResponse.json({ detail: 'Invalid or missing setup token.' }, { status: 403 }),
      ),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: '/setup' })

    await user.type(await screen.findByLabelText(/setup token/i), 'wrong')
    await user.type(screen.getByLabelText(/email/i), 'a@b.c')
    await user.type(screen.getByLabelText(/^password$/i), 'longenough')
    await user.click(screen.getByRole('button', { name: /create admin/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid or missing setup token/i)
  })
})

describe('LoginPage cold-start bounce', () => {
  it('redirects to /setup when needs_admin is true', async () => {
    server.use(
      http.get('/api/setup/status', () => HttpResponse.json({ needs_admin: true })),
    )
    const { router } = renderWithProviders({ initialRoute: '/login' })
    await waitFor(() => expect(router.state.location.pathname).toBe('/setup'))
  })
})
