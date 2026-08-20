import { describe, it, expect, afterEach } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@tests/utils'
import { recordRecentlyViewed } from '@/utils/recentlyViewed'

const WS_ROUTE = '/acme-analytics/worksheets'

async function openPalette() {
  await userEvent.keyboard('{Control>}k{/Control}')
  return screen.findByRole('dialog', { name: /command palette/i })
}

describe('CommandPalette', () => {
  afterEach(() => {
    localStorage.clear()
  })

  it('opens with Ctrl+K and shows static navigation, including the fixed Admin route', async () => {
    const { router } = renderWithProviders({ initialRoute: WS_ROUTE })
    await screen.findByText('events.sql')
    const dialog = await openPalette()

    const admin = await within(dialog).findByText('Admin')
    await userEvent.click(admin)

    // /admin redirects to its storage-tab index route — the regression this
    // guards is landing under /admin at all, not on /compute.
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(
        '/acme-analytics/admin/storage',
      ),
    )
  })

  it('does not search until at least 2 characters are typed', async () => {
    renderWithProviders({ initialRoute: WS_ROUTE })
    const dialog = await openPalette()
    const input = within(dialog).getByPlaceholderText(/search commands/i)

    await userEvent.type(input, 'e')
    // Give any (incorrect) fetch a moment to land, then confirm no result groups appeared.
    await new Promise((r) => setTimeout(r, 300))
    expect(within(dialog).queryByText('Tables')).not.toBeInTheDocument()
    expect(within(dialog).queryByText('Schemas')).not.toBeInTheDocument()
  })

  it('searches tables and schemas with disambiguating parent-path context', async () => {
    renderWithProviders({ initialRoute: WS_ROUTE })
    const dialog = await openPalette()
    const input = within(dialog).getByPlaceholderText(/search commands/i)

    await userEvent.type(input, 'event')

    const tablesGroup = await within(dialog).findByText('Tables')
    const tableItem = tablesGroup
      .closest('[cmdk-group]')!
      .querySelector('[cmdk-item]') as HTMLElement
    expect(tableItem).toHaveTextContent('events')
    expect(tableItem).toHaveTextContent('acme_analytics.raw')
  })

  it('navigates directly to a table result\'s object-detail page', async () => {
    const { router } = renderWithProviders({ initialRoute: WS_ROUTE })
    const dialog = await openPalette()
    const input = within(dialog).getByPlaceholderText(/search commands/i)

    await userEvent.type(input, 'event')
    const tablesGroup = await within(dialog).findByText('Tables')
    const tableItem = tablesGroup
      .closest('[cmdk-group]')!
      .querySelector('[cmdk-item]') as HTMLElement
    await userEvent.click(tableItem)

    await waitFor(() =>
      expect(router.state.location.pathname).toBe(
        '/acme-analytics/catalog/acme_analytics/raw/events',
      ),
    )
  })

  it('shows recently viewed objects for an empty query', async () => {
    recordRecentlyViewed('acme-analytics', {
      type: 'table',
      catalog: 'acme_analytics',
      schema: 'raw',
      name: 'events',
    })
    renderWithProviders({ initialRoute: WS_ROUTE })
    const dialog = await openPalette()

    expect(await within(dialog).findByText('Recent')).toBeInTheDocument()
    expect(within(dialog).getByText('events')).toBeInTheDocument()
  })

  it('opens a saved-query result directly into the worksheet', async () => {
    const { router } = renderWithProviders({ initialRoute: WS_ROUTE })
    const dialog = await openPalette()
    const input = within(dialog).getByPlaceholderText(/search commands/i)

    await userEvent.type(input, 'funnel')
    const savedGroup = await within(dialog).findByText('Saved queries')
    const savedItem = savedGroup
      .closest('[cmdk-group]')!
      .querySelector('[cmdk-item]') as HTMLElement
    await userEvent.click(savedItem)

    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/acme-analytics/worksheets'),
    )
  })
})
