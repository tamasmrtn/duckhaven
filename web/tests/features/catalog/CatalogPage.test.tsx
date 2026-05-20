import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@tests/utils'

const CATALOG_ROUTE = '/acme-analytics/catalog'

describe('CatalogPage', () => {
  it('creates a new schema via the + button', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: CATALOG_ROUTE })

    // The MSW fixture seeds raw + analytics under ws-1 (acme-analytics).
    await screen.findByText('raw')

    await user.click(screen.getByRole('button', { name: /new schema/i }))
    const nameInput = await screen.findByLabelText(/^name$/i)
    await user.type(nameInput, 'gold')
    await user.click(screen.getByRole('button', { name: /^create$/i }))

    await waitFor(() => {
      expect(screen.getByText('gold')).toBeInTheDocument()
    })
  })

  it('creates a new table with column rows', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: CATALOG_ROUTE })

    // Pick the raw schema to expose the table list + add-table button.
    await user.click(await screen.findByRole('button', { name: /raw/i }))
    await screen.findByRole('button', { name: /new table/i })

    await user.click(screen.getByRole('button', { name: /new table/i }))
    await user.type(await screen.findByLabelText(/^name$/i), 'pageviews')
    const columnName = screen.getByLabelText(/column name/i)
    await user.type(columnName, 'id')

    await user.click(screen.getByRole('button', { name: /^create$/i }))

    await waitFor(() => {
      expect(screen.getByText('pageviews')).toBeInTheDocument()
    })
  })
})
