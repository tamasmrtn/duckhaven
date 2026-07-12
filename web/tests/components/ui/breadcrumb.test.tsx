import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Breadcrumb } from '@/components/ui/breadcrumb'

describe('Breadcrumb', () => {
  it('renders labels in order with a separator between each', () => {
    const { container } = render(
      <Breadcrumb
        items={[
          { label: 'acme', emphasis: true },
          { label: 'sales' },
          { label: 'orders', emphasis: true },
        ]}
      />,
    )

    const labels = Array.from(container.querySelectorAll('span')).map(
      (s) => s.textContent,
    )
    expect(labels).toEqual(['acme', 'sales', 'orders'])

    // One ChevronRight (an svg) between each pair of crumbs: items - 1.
    expect(container.querySelectorAll('svg')).toHaveLength(2)
  })

  it('emphasizes only the flagged segments', () => {
    render(
      <Breadcrumb
        items={[
          { label: 'acme', emphasis: true },
          { label: 'sales' },
          { label: 'orders', emphasis: true },
        ]}
      />,
    )

    expect(screen.getByText('acme')).toHaveClass('font-medium', 'text-text-primary')
    expect(screen.getByText('orders')).toHaveClass('font-medium', 'text-text-primary')
    expect(screen.getByText('sales')).not.toHaveClass('font-medium')
  })
})
