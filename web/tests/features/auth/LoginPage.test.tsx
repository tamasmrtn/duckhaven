import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { server } from '@tests/mock/server'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@tests/utils'

describe('LoginPage', () => {
  it('renders email, password inputs and sign-in button', async () => {
    renderWithProviders({ initialRoute: '/login' })
    // Router renders async; use findBy to wait for the component
    expect(await screen.findByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('uses a descriptive password placeholder, not bullets', async () => {
    renderWithProviders({ initialRoute: '/login' })
    const password = await screen.findByLabelText(/password/i)
    expect(password).toHaveAttribute('placeholder')
    expect(password.getAttribute('placeholder')).not.toMatch(/•/)
  })

  it('navigates to first workspace worksheets on successful login', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: '/login' })

    await user.type(await screen.findByLabelText(/email/i), 'test@example.com')
    await user.type(screen.getByLabelText(/password/i), 'password')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    // After login, the sign-in button disappears (navigated away from login page)
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /sign in/i })).not.toBeInTheDocument()
    })
  })

  it('navigates to /welcome when the user has no workspaces', async () => {
    server.use(http.get('/api/workspaces', () => HttpResponse.json([])))
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: '/login' })

    await user.type(await screen.findByLabelText(/email/i), 'test@example.com')
    await user.type(screen.getByLabelText(/password/i), 'password')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/welcome'))
  })

  it('shows error message on 401 response', async () => {
    server.use(
      http.post('/api/auth/login', () => new HttpResponse(null, { status: 401 })),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: '/login' })

    await user.type(await screen.findByLabelText(/email/i), 'wrong@example.com')
    await user.type(screen.getByLabelText(/password/i), 'wrong')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument()
    })
  })

  it('shows a production-ready credentials error without a dev hint', async () => {
    server.use(
      http.post('/api/auth/login', () => new HttpResponse(null, { status: 401 })),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: '/login' })

    await user.type(await screen.findByLabelText(/email/i), 'wrong@example.com')
    await user.type(screen.getByLabelText(/password/i), 'wrong')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Invalid credentials.')
    expect(alert).not.toHaveTextContent(/try any email and password/i)
  })

  it('renders a button per configured OIDC provider', async () => {
    server.use(
      http.get('/api/auth/methods', () =>
        HttpResponse.json({
          local: true,
          ldap: false,
          oidc_providers: [
            { id: 'entra', label: 'Microsoft' },
            { id: 'google', label: 'Google' },
          ],
        }),
      ),
    )
    renderWithProviders({ initialRoute: '/login' })
    expect(
      await screen.findByRole('button', { name: /sign in with microsoft/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /sign in with google/i }),
    ).toBeInTheDocument()
  })

  it('hides the SSO button when OIDC is disabled', async () => {
    renderWithProviders({ initialRoute: '/login' })
    await screen.findByLabelText(/email/i)
    expect(
      screen.queryByRole('button', { name: /sign in with/i }),
    ).not.toBeInTheDocument()
  })

  it('disables submit button while pending', async () => {
    server.use(
      http.post('/api/auth/login', async () => {
        await new Promise<void>((r) => setTimeout(r, 100))
        return new HttpResponse(null, { status: 401 })
      }),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: '/login' })

    await user.type(await screen.findByLabelText(/email/i), 'test@example.com')
    await user.type(screen.getByLabelText(/password/i), 'pass')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(screen.getByRole('button', { name: /signing in/i })).toBeDisabled()
  })
})
