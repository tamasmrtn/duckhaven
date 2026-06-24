import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'

// LeftRail only consumes these two router hooks; stub them so the nav can be
// rendered in isolation for an icon assertion.
vi.mock('@tanstack/react-router', () => ({
  useRouterState: () => ({ location: { pathname: '/acme/worksheets' } }),
  useNavigate: () => vi.fn(),
}))

import { LeftRail } from '@/components/app/LeftRail'

describe('LeftRail icons', () => {
  it('uses a bookmarked book for Saved queries and an open book for Catalog', () => {
    const { getByLabelText } = render(<LeftRail ws="acme" />)

    expect(
      getByLabelText('Saved queries').querySelector('svg.lucide-book-marked'),
    ).toBeTruthy()
    expect(
      getByLabelText('Catalog').querySelector('svg.lucide-book-open'),
    ).toBeTruthy()
  })

  it('no longer uses the ambiguous database glyph in the nav', () => {
    const { container } = render(<LeftRail ws="acme" />)
    expect(container.querySelector('svg.lucide-database')).toBeNull()
  })
})
