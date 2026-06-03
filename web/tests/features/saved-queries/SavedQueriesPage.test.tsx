import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@tests/utils'

const ROUTE = '/acme-analytics/saved-queries'

describe('SavedQueriesPage', () => {
  it('lists saved queries for the workspace', async () => {
    renderWithProviders({ initialRoute: ROUTE })
    expect(await screen.findByText('Daily events')).toBeInTheDocument()
  })

  it('"Open in worksheet" navigates to the worksheet with the SQL seeded', async () => {
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: ROUTE })
    await screen.findByText('Daily events')

    const buttons = screen.getAllByRole('button', { name: 'Open in worksheet' })
    await user.click(buttons[0])

    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/acme-analytics/worksheets')
    })
    // The worksheet consumes the stash on mount and seeds a "from catalog" tab,
    // proving the SQL was handed off end-to-end via the shared stash channel.
    expect(await screen.findByText('from catalog')).toBeInTheDocument()
  })
})
