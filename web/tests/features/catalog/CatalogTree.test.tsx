import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClientProvider } from '@tanstack/react-query'
import {
  createRootRoute,
  createRouter,
  createMemoryHistory,
  RouterProvider,
} from '@tanstack/react-router'
import { CatalogTree } from '@/features/catalog/CatalogTree'
import { createTestQueryClient, createWrapper } from '@tests/utils'
import { server } from '@tests/mock/server'

// The hover-card's "View details"/"Lineage"/"Permissions" links use
// TanStack's <Link>, which needs a real router context — a single-route
// router standing in for the app shell is enough for that, without pulling
// in the full app route tree these are otherwise isolated component tests.
function renderTree(
  onTableClick: (catalog: string, schema: string, table: string) => void,
) {
  const queryClient = createTestQueryClient()
  const rootRoute = createRootRoute({
    component: () => (
      <CatalogTree
        ws="acme-analytics"
        workspaceName="acme-analytics"
        onTableClick={onTableClick}
      />
    ),
  })
  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: ['/'] }),
    defaultPendingMinMs: 0,
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

describe('CatalogTree', () => {
  it('reports the catalog, schema and table when a table row is clicked', async () => {
    const onTableClick = vi.fn()
    renderTree(onTableClick)

    const events = await screen.findByRole('button', { name: /events/i })
    fireEvent.click(events)
    expect(onTableClick).toHaveBeenCalledWith('acme_analytics', 'raw', 'events')
  })

  it('makes the table row a drag source carrying its fully-qualified name', async () => {
    renderTree(() => {})
    const events = await screen.findByRole('button', { name: /events/i })
    expect(events).toHaveAttribute('draggable', 'true')

    const setData = vi.fn()
    fireEvent.dragStart(events, { dataTransfer: { setData, effectAllowed: '' } })
    expect(setData).toHaveBeenCalledWith('text/plain', 'acme_analytics.raw.events')
  })

  it('filters table rows by the search box', async () => {
    renderTree(() => {})

    // Schemas auto-expand, so sibling tables are visible up front.
    await screen.findByRole('button', { name: /events/i })
    expect(
      screen.getByRole('button', { name: /page_views/i }),
    ).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Search tables'), 'events')

    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: /page_views/i }),
      ).not.toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: /events/i })).toBeInTheDocument()
  })

  it('expands a table to reveal its columns', async () => {
    renderTree(() => {})

    const eventsName = await screen.findByRole('button', { name: /events/i })
    // Columns are not shown until the row is expanded.
    expect(screen.queryByText('event_id')).not.toBeInTheDocument()

    // The expand toggle sits alongside the table name in the same row.
    const row = eventsName.closest('div') as HTMLElement
    fireEvent.click(within(row).getByRole('button', { name: /show columns/i }))

    expect(await screen.findByText('event_id')).toBeInTheDocument()
    expect(screen.getByText('event_type')).toBeInTheDocument()
  })

  it('renders the catalog node with a closed-book icon, not a database glyph', async () => {
    renderTree(() => {})

    const catalog = await screen.findByRole('button', { name: /acme_analytics/i })
    expect(catalog.querySelector('svg.lucide-book')).toBeTruthy()
    expect(catalog.querySelector('svg.lucide-database')).toBeNull()
  })

  it('shows a storage-backend indicator on the catalog node', async () => {
    renderTree(() => {})

    // acme_analytics is an S3-backed catalog (fixtures) -> the Box glyph.
    const catalog = await screen.findByRole('button', { name: /acme_analytics/i })
    expect(catalog.querySelector('svg.lucide-box')).toBeTruthy()
  })

  it('opens Catalog information from the right-click menu with backend metadata', async () => {
    renderTree(() => {})

    const catalog = await screen.findByRole('button', { name: /acme_analytics/i })
    fireEvent.contextMenu(catalog)

    await userEvent.click(
      await screen.findByRole('menuitem', { name: /catalog information/i }),
    )

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Catalog information')).toBeInTheDocument()
    expect(within(dialog).getByText(/AWS S3/)).toBeInTheDocument()
    expect(
      within(dialog).getByText('s3://acme-data/duckhaven/'),
    ).toBeInTheDocument()
  })

  it('shows a read-only information_schema node with its supported views', async () => {
    renderTree(() => {})
    // Wait for the default catalog to expand and load.
    await screen.findByRole('button', { name: /events/i })

    const infoNode = screen.getByRole('button', { name: /information_schema/i })
    // Read-only signalling: a lock icon and a "read-only" badge.
    expect(within(infoNode).getByText(/read-only/i)).toBeInTheDocument()
    expect(infoNode.querySelector('svg.lucide-lock')).toBeTruthy()

    // Expanding reveals the supported views.
    await userEvent.click(infoNode)
    for (const view of ['schemata', 'tables', 'views']) {
      expect(screen.getByRole('button', { name: view })).toBeInTheDocument()
    }
    // `columns` is not offered: DuckDB cannot introspect an attached Iceberg
    // relation's columns through it, so the seeded query would return an
    // UNKNOWN placeholder rather than the table's real columns.
    expect(
      screen.queryByRole('button', { name: 'columns' }),
    ).not.toBeInTheDocument()
  })

  it('makes an information_schema view row a drag source', async () => {
    renderTree(() => {})
    await screen.findByRole('button', { name: /events/i })
    await userEvent.click(
      screen.getByRole('button', { name: /information_schema/i }),
    )

    const tables = screen.getByRole('button', { name: 'tables' })
    expect(tables).toHaveAttribute('draggable', 'true')

    const setData = vi.fn()
    fireEvent.dragStart(tables, { dataTransfer: { setData, effectAllowed: '' } })
    expect(setData).toHaveBeenCalledWith(
      'text/plain',
      'information_schema.tables',
    )
  })

  it('hides the information_schema node for a scoped catalog', async () => {
    // Under a scoped attachment the API rejects these views: DuckDB computes
    // them across every attached catalog and cannot narrow them to the
    // principal's grants, so listing tables through them would leak. Offering
    // the node would only lead the user to a 403.
    server.use(
      http.get('/api/workspaces/:ws/catalogs', () =>
        HttpResponse.json([
          {
            id: 'cat-scoped',
            slug: 'acme_analytics',
            name: 'acme_analytics',
            polaris_name: 'acme_analytics',
            storage_backend_kind: 's3',
            is_default: true,
            attached_workspaces: 1,
            access_mode: 'scoped',
          },
        ]),
      ),
    )
    renderTree(() => {})

    await screen.findByRole('button', { name: /events/i })
    expect(
      screen.queryByRole('button', { name: /information_schema/i }),
    ).not.toBeInTheDocument()
  })

  it('seeds a scoped query when an information_schema view is clicked', async () => {
    const onMetaViewClick = vi.fn()
    const { wrapper: Wrapper } = createWrapper()
    render(
      <CatalogTree
        ws="acme-analytics"
        workspaceName="acme-analytics"
        onTableClick={() => {}}
        onMetaViewClick={onMetaViewClick}
      />,
      { wrapper: Wrapper },
    )
    await screen.findByRole('button', { name: /events/i })

    await userEvent.click(
      screen.getByRole('button', { name: /information_schema/i }),
    )
    await userEvent.click(screen.getByRole('button', { name: 'tables' }))
    expect(onMetaViewClick).toHaveBeenCalledWith('acme_analytics', 'tables')
  })

  it('opens the new-catalog dialog from the create dropdown', async () => {
    renderTree(() => {})

    await userEvent.click(
      await screen.findByRole('button', { name: /^create$/i }),
    )
    await userEvent.click(
      await screen.findByRole('menuitem', { name: /create catalog/i }),
    )

    expect(await screen.findByText('New catalog')).toBeInTheDocument()
  })

  it('creates a schema in a chosen catalog from the create dropdown', async () => {
    renderTree(() => {})
    // Wait for the tree (and its catalogs) to load.
    await screen.findByRole('button', { name: /events/i })

    await userEvent.click(screen.getByRole('button', { name: /^create$/i }))
    await userEvent.click(
      await screen.findByRole('menuitem', { name: /create schema/i }),
    )

    // The dialog offers a catalog picker; it defaults to the default catalog.
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('New schema')).toBeInTheDocument()
    expect(within(dialog).getByLabelText(/catalog/i)).toBeInTheDocument()

    await userEvent.type(within(dialog).getByLabelText(/^name$/i), 'gold')
    await userEvent.click(
      within(dialog).getByRole('button', { name: /^create$/i }),
    )

    // The new schema appears under the (default) catalog node.
    expect(await screen.findByText('gold')).toBeInTheDocument()
  })

  it('refetches the catalog when the refresh button is clicked', async () => {
    renderTree(() => {})

    // Wait for the initial catalog load.
    await screen.findByRole('button', { name: /events/i })

    // The catalog now reports a schema created out-of-band (e.g. from the
    // worksheet); the refresh button must surface it.
    server.use(
      http.get('/api/workspaces/:ws/catalogs/:catalog/schemas', () =>
        HttpResponse.json([
          { name: 'fresh_schema', catalog: 'acme_analytics', workspace_id: 'x' },
        ]),
      ),
    )

    await userEvent.click(screen.getByRole('button', { name: /refresh catalog/i }))

    expect(
      await screen.findByRole('button', { name: /fresh_schema/i }),
    ).toBeInTheDocument()
  })

  it('shows a hover-preview card with table stats', async () => {
    renderTree(() => {})
    const eventsName = await screen.findByRole('button', { name: /events/i })
    const row = eventsName.closest('div') as HTMLElement

    await userEvent.hover(row)

    // "Marton" (owner) only ever appears inside the hover card — the
    // collapsed row itself already shows a "42.1M" row-count badge, so that
    // text alone can't distinguish "the card loaded" from "the row rendered".
    expect(
      await screen.findByText('Marton', undefined, { timeout: 2000 }),
    ).toBeInTheDocument()
    expect(screen.getByText('312.0 MB')).toBeInTheDocument()
    expect(screen.getByText('acme_analytics.raw')).toBeInTheDocument()
  })

  it('links the hover card to the table\'s details, lineage, and permissions', async () => {
    renderTree(() => {})
    const eventsName = await screen.findByRole('button', { name: /events/i })
    const row = eventsName.closest('div') as HTMLElement

    await userEvent.hover(row)

    const details = await screen.findByRole('link', { name: /view details/i })
    expect(details).toHaveAttribute(
      'href',
      '/acme-analytics/catalog/acme_analytics/raw/events',
    )
    const lineage = screen.getByRole('link', { name: /^lineage$/i })
    expect(lineage).toHaveAttribute(
      'href',
      '/acme-analytics/catalog/acme_analytics/raw/events?tab=lineage',
    )
    const permissions = screen.getByRole('link', { name: /^permissions$/i })
    expect(permissions).toHaveAttribute(
      'href',
      '/acme-analytics/catalog/acme_analytics/raw/events?tab=permissions',
    )
  })

  it('shares the table-detail fetch between expand and hover (no duplicate request)', async () => {
    let requests = 0
    const onStart = ({ request }: { request: Request }) => {
      if (/\/tables\/events(\?|$)/.test(request.url)) requests++
    }
    server.events.on('request:start', onStart)
    try {
      renderTree(() => {})

      const eventsName = await screen.findByRole('button', { name: /events/i })
      const row = eventsName.closest('div') as HTMLElement
      fireEvent.click(within(row).getByRole('button', { name: /show columns/i }))
      await screen.findByText('event_id')
      expect(requests).toBe(1)

      await userEvent.hover(row)
      await screen.findByText('Marton', undefined, { timeout: 2000 })
      expect(requests).toBe(1)
    } finally {
      server.events.removeListener('request:start', onStart)
    }
  })

  it('probes row-count stats via the refresh endpoint on refresh', async () => {
    let probed = false
    server.use(
      http.post('/api/workspaces/:ws/schemas/refresh-stats', () => {
        probed = true
        return HttpResponse.json({ probed: 1 })
      }),
    )
    renderTree(() => {})
    await screen.findByRole('button', { name: /events/i })

    await userEvent.click(screen.getByRole('button', { name: /refresh catalog/i }))

    await waitFor(() => expect(probed).toBe(true))
  })
})
