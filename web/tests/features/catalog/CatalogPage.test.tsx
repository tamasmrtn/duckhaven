import { describe, it, expect } from 'vitest'
import { screen, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'

const CATALOG_ROUTE = '/acme-analytics/catalog'

describe('CatalogPage', () => {
  it('creates a schema via the catalog right-click menu', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: CATALOG_ROUTE })

    // The MSW fixture seeds raw + analytics under ws-1 (acme-analytics).
    await screen.findByText('raw')

    fireEvent.contextMenu(screen.getByText(/right-click to manage/i))
    await user.click(
      await screen.findByRole('menuitem', { name: /create schema/i }),
    )
    await user.type(await screen.findByLabelText(/^name$/i), 'gold')
    await user.click(screen.getByRole('button', { name: /^create$/i }))

    await waitFor(() => {
      expect(screen.getByText('gold')).toBeInTheDocument()
    })
  })

  it('creates a table via the schema right-click menu', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: CATALOG_ROUTE })

    const rawRow = await screen.findByRole('button', { name: /raw/i })
    fireEvent.contextMenu(rawRow)
    await user.click(
      await screen.findByRole('menuitem', { name: /create table/i }),
    )
    await user.type(await screen.findByLabelText(/^name$/i), 'pageviews')
    await user.type(screen.getByLabelText(/column name/i), 'id')
    await user.click(screen.getByRole('button', { name: /^create$/i }))

    // Open the schema to view its tables and confirm the new one shows.
    await user.click(rawRow)
    await waitFor(() => {
      expect(screen.getByText('pageviews')).toBeInTheDocument()
    })
  })

  it('shows an empty state when a schema has no tables', async () => {
    server.use(
      http.get('/api/workspaces/:ws/schemas/:schema/tables', () =>
        HttpResponse.json([]),
      ),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: CATALOG_ROUTE })

    await user.click(await screen.findByRole('button', { name: /raw/i }))

    expect(
      await screen.findByText(/no tables in this schema/i),
    ).toBeInTheDocument()
  })

  it('drops a non-empty schema with cascade via the schema menu', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: CATALOG_ROUTE })

    const rawRow = await screen.findByRole('button', { name: /raw/i })
    fireEvent.contextMenu(rawRow)
    await user.click(
      await screen.findByRole('menuitem', { name: /drop schema/i }),
    )

    const dialog = await screen.findByRole('dialog')
    await user.type(
      within(dialog).getByLabelText(/type .* to confirm/i),
      'raw',
    )
    // First attempt hits 409 (non-empty) and reveals the cascade option.
    await user.click(within(dialog).getByRole('button', { name: /drop schema/i }))
    const cascade = await within(dialog).findByLabelText(/also drop all tables/i)
    await user.click(cascade)
    await user.click(within(dialog).getByRole('button', { name: /drop schema/i }))

    await waitFor(() => {
      expect(screen.queryByText('raw')).not.toBeInTheDocument()
    })
  })

  it('drops a table from the detail view and returns to the catalog', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: '/acme-analytics/catalog/raw/events' })

    await user.click(await screen.findByRole('button', { name: /^drop$/i }))
    const dialog = await screen.findByRole('dialog')
    await user.type(
      within(dialog).getByLabelText(/type .* to confirm/i),
      'events',
    )
    await user.click(within(dialog).getByRole('button', { name: /drop table/i }))

    expect(
      await screen.findByRole('heading', { name: 'Catalog' }),
    ).toBeInTheDocument()
  })

  it('surfaces Iceberg-native metadata on the table detail view', async () => {
    renderWithProviders({ initialRoute: '/acme-analytics/catalog/raw/events' })

    // The events fixture carries format version, snapshot, file count, deletes.
    expect(await screen.findByText(/Iceberg v2/)).toBeInTheDocument()
    expect(screen.getByText(/128 files/)).toBeInTheDocument()
    expect(
      screen.getByText(/snapshot 7264354987654321234/),
    ).toBeInTheDocument()
    expect(screen.getByText('has deletes')).toBeInTheDocument()
  })

  it('routes "Alter table" into a worksheet seeded with ALTER SQL', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: '/acme-analytics/catalog/raw/events' })

    await user.click(await screen.findByRole('button', { name: /alter table/i }))

    // Navigates to the worksheet, seeding a new tab from the catalog action.
    expect(await screen.findByText(/from catalog/i)).toBeInTheDocument()
  })
})
