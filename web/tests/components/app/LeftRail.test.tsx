import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

// LeftRail only consumes these two router hooks; stub them so the nav can be
// rendered in isolation. Tests may point it at another path.
const route = vi.hoisted(() => ({ pathname: '/acme/worksheets' }))
vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => ({ location: { pathname: route.pathname } }),
  useNavigate: () => vi.fn(),
}))

import { LeftRail } from '@/components/app/LeftRail'

function renderRail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <LeftRail ws="acme" />
    </QueryClientProvider>,
  )
}

describe('LeftRail icons', () => {
  it('uses a bookmarked book for Saved queries and an open book for Catalog', () => {
    const { getByLabelText } = renderRail()

    expect(
      getByLabelText('Saved queries').querySelector('svg.lucide-book-marked'),
    ).toBeTruthy()
    expect(
      getByLabelText('Catalog').querySelector('svg.lucide-book-open'),
    ).toBeTruthy()
  })

  it('no longer uses the ambiguous database glyph in the nav', () => {
    const { container } = renderRail()
    expect(container.querySelector('svg.lucide-database')).toBeNull()
  })

  it('uses a microchip for Compute', () => {
    const { getByLabelText } = renderRail()

    expect(
      getByLabelText('Compute').querySelector('svg.lucide-microchip'),
    ).toBeTruthy()
  })

  it('shows Compute to a user holding no global permission', () => {
    // The whole point of moving it out of /admin: a per-agent grantee must be
    // able to reach their agent's monitoring page. The page itself lists only
    // the agents the server says they can see.
    const { getByLabelText, queryByLabelText } = renderRail()

    expect(getByLabelText('Compute')).toBeTruthy()
    // `me` never resolves in this isolated render, so no permissions are held.
    expect(queryByLabelText('Admin')).toBeNull()
  })
})

describe('LeftRail active item', () => {
  // Regression: matching "/catalog" anywhere in the path lit Catalog up on
  // Admin > Catalog access (/acme/admin/catalog-access) as well as Admin.
  it('marks the destination by its own route segment only', () => {
    route.pathname = '/acme/admin/catalog-access'
    renderRail()
    expect(screen.getByRole('button', { name: 'Catalog' })).not.toHaveAttribute('aria-current')
  })

  it('still marks a destination on its nested pages', () => {
    route.pathname = '/acme/catalog/tpch/sf10'
    renderRail()
    expect(screen.getByRole('button', { name: 'Catalog' })).toHaveAttribute('aria-current', 'page')
  })
})
