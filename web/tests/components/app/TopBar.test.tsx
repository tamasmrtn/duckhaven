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
