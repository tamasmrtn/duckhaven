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
