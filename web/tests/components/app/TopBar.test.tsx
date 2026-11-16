import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@tests/utils'

const ROUTE = '/acme-analytics/worksheets'

describe('TopBar user menu', () => {
  it('navigates to Settings, using navItems.ts\'s label for the entry', async () => {
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: ROUTE })

    await user.click(await screen.findByRole('button', { name: /marton/i }))
    await user.click(await screen.findByRole('menuitem', { name: 'Settings' }))

    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/acme-analytics/settings'),
    )
  })
})

describe('TopBar user menu accessible name', () => {
  it('is named by aria-label, so it survives the sm-only text being hidden', async () => {
    renderWithProviders({ initialRoute: ROUTE })
    // Waits for /me to resolve; the label falls back to "Account menu" before then.
    const button = await screen.findByRole('button', { name: /^account menu, .*marton/i })
    expect(button).toHaveAttribute('aria-label', expect.stringMatching(/^Account menu, /))
  })
})

describe('TopBar navigation toggle', () => {
  it('says whether the navigation is expanded, and flips it', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })
    const toggle = await screen.findByRole('button', { name: 'Toggle navigation' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(toggle).toHaveAttribute('aria-controls', 'main-navigation')

    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toHaveAttribute(
      'data-mode',
      'collapsed',
    )
  })
})
