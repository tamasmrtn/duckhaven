import { describe, it, expect } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { renderWithProviders } from '@tests/utils'
import { server } from '@tests/mock/server'
import { CURRENT_USER } from '@/mock/fixtures/users'

const ROUTE = '/acme-analytics/settings'

describe('SettingsPage — Workspace tab', () => {
  it('shows the current name and description, and saves an update', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })

    const nameInput = (await screen.findByLabelText('Name')) as HTMLInputElement
    expect(nameInput.value).toBe('acme-analytics')

    await user.clear(nameInput)
    await user.type(nameInput, 'Renamed workspace')
    await user.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(nameInput.value).toBe('Renamed workspace'))
  })

  it('requires typing the workspace name to confirm delete', async () => {
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: ROUTE })
    await screen.findByLabelText('Name')

    await user.click(screen.getByRole('button', { name: /delete workspace/i }))
    const dialog = await screen.findByRole('dialog')
    const confirmBtn = within(dialog).getByRole('button', {
      name: /^delete workspace$/i,
    })
    expect(confirmBtn).toBeDisabled()

    await user.type(
      within(dialog).getByLabelText(/type .* to confirm/i),
      'acme-analytics',
    )
    expect(confirmBtn).not.toBeDisabled()
    await user.click(confirmBtn)

    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/welcome'),
    )
  })

  it('disables rename and delete for a non-owner', async () => {
    server.use(
      http.get('/api/me', () =>
        HttpResponse.json({ ...CURRENT_USER, id: 'u-3' }),
      ),
    )
    renderWithProviders({ initialRoute: ROUTE })

    const nameInput = await screen.findByLabelText('Name')
    expect(nameInput).toBeDisabled()
    expect(
      screen.getByRole('button', { name: /delete workspace/i }),
    ).toBeDisabled()
  })
})

describe('SettingsPage — Account tab', () => {
  it('shows profile info', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByLabelText('Name')

    await user.click(screen.getByRole('tab', { name: /account/i }))

    // "Marton" also appears in the TopBar's own user-menu trigger — scope to
    // the Account tab panel so this only asserts on the profile section.
    const panel = await screen.findByRole('tabpanel')
    expect(await within(panel).findByText(CURRENT_USER.email)).toBeInTheDocument()
    expect(within(panel).getByText(CURRENT_USER.name)).toBeInTheDocument()
  })

  it('keeps the theme control in sync with the top-bar toggle', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByLabelText('Name')
    await user.click(screen.getByRole('tab', { name: /account/i }))

    const darkOption = await screen.findByRole('radio', { name: /dark/i })
    await user.click(darkOption)

    await waitFor(() => expect(darkOption).toHaveAttribute('aria-checked', 'true'))
    // The top-bar theme dropdown trigger is mounted at the same time (same
    // AppShell) — its icon must reflect the same ThemeProvider state.
    expect(
      screen.getByRole('button', { name: /change theme/i }).querySelector('svg.lucide-moon'),
    ).toBeInTheDocument()
  })
})
