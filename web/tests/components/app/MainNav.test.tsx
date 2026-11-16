import { describe, it, expect, afterEach, vi } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'
import { CURRENT_USER } from '@/mock/fixtures/users'
import type { NavMode } from '@/types/auth'

const ROUTE = '/acme-analytics/saved-queries'

function accountChose(sidebar: NavMode) {
  server.use(http.get('/api/me', () => HttpResponse.json({ ...CURRENT_USER, sidebar })))
}

async function renderNav(initialRoute = ROUTE) {
  const view = renderWithProviders({ initialRoute })
  const nav = await screen.findByRole('navigation', { name: 'Main navigation' })
  await within(nav).findByRole('link', { name: 'Worksheets' })
  // The account has answered once the user menu carries a name.
  await screen.findByRole('button', { name: /^Account menu, / })
  return { ...view, nav, links: within(nav) }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('MainNav groups', () => {
  it('names its four groups, in order, each holding its pages', async () => {
    const { links } = await renderNav()

    const groups = links.getAllByRole('group')
    expect(groups.map((g) => g.getAttribute('aria-label'))).toEqual([
      'SQL',
      'Data',
      'Analytics',
      'Manage',
    ])
    const names = (group: HTMLElement) =>
      within(group).getAllByRole('link').map((l) => l.textContent)
    expect(names(groups[0])).toEqual(['Worksheets', 'Saved queries', 'Schedules', 'History'])
    expect(names(groups[1])).toEqual(['Catalog', 'Lakehouse health'])
    expect(names(groups[2])).toEqual(['Dashboards', 'Semantic models'])
    expect(names(groups[3])).toEqual(['Compute', 'Admin'])
  })

  it('keeps its group names out of the heading outline', async () => {
    const { links } = await renderNav()

    expect(links.queryAllByRole('heading')).toEqual([])
  })

  it('gives Settings and Connections no entry: the user menu and History hold them', async () => {
    const { links } = await renderNav()

    expect(links.queryByRole('link', { name: 'Settings' })).toBeNull()
    expect(links.queryByRole('link', { name: 'Connections' })).toBeNull()
  })

  it('uses a bookmark for Saved queries, an open book for Catalog and a microchip for Compute', async () => {
    const { links, nav } = await renderNav()

    // Two books side by side read as one thing; a bookmark is what a saved
    // query is.
    expect(
      links.getByRole('link', { name: 'Saved queries' }).querySelector('svg.lucide-bookmark'),
    ).toBeTruthy()
    expect(
      links.getByRole('link', { name: 'Catalog' }).querySelector('svg.lucide-book-open'),
    ).toBeTruthy()
    expect(
      links.getByRole('link', { name: 'Compute' }).querySelector('svg.lucide-microchip'),
    ).toBeTruthy()
    expect(nav.querySelector('svg.lucide-database')).toBeNull()
  })

  it('shows Compute but not Admin to a user holding no global permission', async () => {
    // A per-agent grantee must be able to reach their agent's monitoring page.
    server.use(
      http.get('/api/me', () =>
        HttpResponse.json({ ...CURRENT_USER, role: 'user', permissions: [] }),
      ),
    )
    const { links } = await renderNav()

    expect(links.getByRole('link', { name: 'Compute' })).toBeInTheDocument()
    expect(links.queryByRole('link', { name: 'Admin' })).toBeNull()
  })

  it('is made of real links, so a page can open in a new tab', async () => {
    const { links } = await renderNav()

    expect(links.getByRole('link', { name: 'Catalog' })).toHaveAttribute(
      'href',
      '/acme-analytics/catalog',
    )
  })
})

describe('MainNav current page', () => {
  it('marks a destination on its nested pages', async () => {
    const { links } = await renderNav('/acme-analytics/catalog/acme_analytics')

    expect(links.getByRole('link', { name: 'Catalog' })).toHaveAttribute('aria-current', 'page')
  })

  // Regression: matching "/catalog" anywhere in the path lit Catalog up on
  // Admin > Catalog access (/acme/admin/catalog-access) as well as Admin.
  it('marks a destination by its own route segment only', async () => {
    const { links } = await renderNav('/acme-analytics/admin/catalog-access')

    expect(await links.findByRole('link', { name: 'Admin' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(links.getByRole('link', { name: 'Catalog' })).not.toHaveAttribute('aria-current')
  })

  it('marks History on a connection and on a query profile', async () => {
    for (const path of ['/acme-analytics/sessions/sess-live', '/acme-analytics/queries/q-1']) {
      const { links, unmount } = await renderNav(path)
      expect(links.getByRole('link', { name: 'History' })).toHaveAttribute('aria-current', 'page')
      unmount()
    }
  })
})

describe('MainNav modes', () => {
  it('is expanded, with its labels on screen, for a user who never chose', async () => {
    const { nav, links } = await renderNav()

    expect(nav).toHaveAttribute('data-mode', 'expanded')
    expect(links.getByText('Catalog')).not.toHaveClass('sr-only')
    expect(links.getByText('SQL')).toBeInTheDocument()
  })

  it('shows no tooltip while the labels are on screen', async () => {
    const { links } = await renderNav()

    act(() => links.getByRole('link', { name: 'Catalog' }).focus())

    expect(screen.queryByRole('tooltip')).toBeNull()
  })

  it('collapses to the icon rail, keeping every name for assistive tech', async () => {
    accountChose('collapsed')
    const { nav, links } = await renderNav()

    await waitFor(() => expect(nav).toHaveAttribute('data-mode', 'collapsed'))
    expect(links.getByText('Catalog')).toHaveClass('sr-only')
    expect(links.getByRole('link', { name: 'Catalog' })).toBeInTheDocument()
    expect(links.getByRole('group', { name: 'Data' })).toBeInTheDocument()
    expect(links.queryByText('SQL')).toBeNull()

    act(() => links.getByRole('link', { name: 'Catalog' }).focus())

    expect(await screen.findByRole('tooltip')).toHaveTextContent('Catalog')
  })
})

describe('MainNav expand on hover', () => {
  async function renderHoverNav() {
    accountChose('hover')
    const view = await renderNav()
    await waitFor(() => expect(view.nav).toHaveAttribute('data-mode', 'hover'))
    // After the data has loaded: MSW and the router run on real timers.
    vi.useFakeTimers()
    return view
  }

  const isOpen = (nav: HTMLElement) => nav.hasAttribute('data-open')

  it('opens after the pointer rests, over the page rather than beside it', async () => {
    const { nav, links } = await renderHoverNav()

    fireEvent.pointerEnter(nav)
    act(() => vi.advanceTimersByTime(199))
    expect(isOpen(nav)).toBe(false)
    act(() => vi.advanceTimersByTime(1))

    expect(isOpen(nav)).toBe(true)
    expect(links.getByText('Catalog')).not.toHaveClass('sr-only')
    // Its slot in the layout stays the rail's width, so the page never moves.
    expect(nav.parentElement).toHaveClass('w-12')
  })

  it('closes a moment after the pointer leaves', async () => {
    const { nav } = await renderHoverNav()
    fireEvent.pointerEnter(nav)
    act(() => vi.advanceTimersByTime(200))

    fireEvent.pointerLeave(nav)
    act(() => vi.advanceTimersByTime(299))
    expect(isOpen(nav)).toBe(true)
    act(() => vi.advanceTimersByTime(1))

    expect(isOpen(nav)).toBe(false)
  })

  it('does not open for a pointer passing across it', async () => {
    const { nav } = await renderHoverNav()

    fireEvent.pointerEnter(nav)
    act(() => vi.advanceTimersByTime(100))
    fireEvent.pointerLeave(nav)
    act(() => vi.advanceTimersByTime(1000))

    expect(isOpen(nav)).toBe(false)
  })

  it('closes on Escape', async () => {
    const { nav } = await renderHoverNav()
    fireEvent.pointerEnter(nav)
    act(() => vi.advanceTimersByTime(200))

    fireEvent.keyDown(window, { key: 'Escape' })

    expect(isOpen(nav)).toBe(false)
  })

  it('opens for keyboard focus and closes when focus leaves', async () => {
    const { nav, links } = await renderHoverNav()

    fireEvent.focus(links.getByRole('link', { name: 'Catalog' }))
    expect(isOpen(nav)).toBe(true)

    fireEvent.blur(links.getByRole('link', { name: 'Catalog' }), {
      relatedTarget: document.body,
    })
    expect(isOpen(nav)).toBe(false)
  })

  it('closes once a page is chosen', async () => {
    const { nav, links } = await renderHoverNav()
    fireEvent.pointerEnter(nav)
    act(() => vi.advanceTimersByTime(200))

    fireEvent.click(links.getByRole('link', { name: 'Catalog' }))

    expect(isOpen(nav)).toBe(false)
  })

  // Regression: with the pointer still on the rail after a click, the panel
  // stayed shut, unlabelled and without tooltips, until the pointer left.
  it('opens again when the pointer moves on the rail after a page is chosen', async () => {
    const { nav, links } = await renderHoverNav()
    fireEvent.pointerEnter(nav)
    act(() => vi.advanceTimersByTime(200))
    fireEvent.click(links.getByRole('link', { name: 'Catalog' }))

    fireEvent.pointerMove(nav)
    act(() => vi.advanceTimersByTime(199))
    expect(isOpen(nav)).toBe(false)
    act(() => vi.advanceTimersByTime(1))

    expect(isOpen(nav)).toBe(true)
  })

  it('stays closed after Escape until the pointer leaves and comes back', async () => {
    const { nav } = await renderHoverNav()
    fireEvent.pointerEnter(nav)
    act(() => vi.advanceTimersByTime(200))
    fireEvent.keyDown(window, { key: 'Escape' })

    fireEvent.pointerMove(nav)
    act(() => vi.advanceTimersByTime(1000))
    expect(isOpen(nav)).toBe(false)

    fireEvent.pointerLeave(nav)
    fireEvent.pointerEnter(nav)
    act(() => vi.advanceTimersByTime(200))
    expect(isOpen(nav)).toBe(true)
  })

  it('shows no tooltips, since opening shows the labels', async () => {
    const { links } = await renderHoverNav()

    fireEvent.focus(links.getByRole('link', { name: 'Catalog' }))
    act(() => vi.advanceTimersByTime(500))

    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})
