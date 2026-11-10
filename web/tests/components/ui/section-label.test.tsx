import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SectionLabel } from '@/components/ui/section-label'

describe('SectionLabel', () => {
  // Regression: five hand-written variants of this label had drifted apart in
  // size, weight and colour.
  // The page title is an h1, so the default must be an h2 or every page skips a level.
  it('renders a heading in the one shared style', () => {
    render(<SectionLabel className="mb-2">Schema</SectionLabel>)
    const label = screen.getByRole('heading', { level: 2, name: 'Schema' })
    expect(label).toHaveClass('text-xs', 'font-semibold', 'uppercase', 'text-text-secondary', 'mb-2')
  })

  it('takes a lower heading level when nested under another label', () => {
    render(<SectionLabel as="h3">Scan effectiveness</SectionLabel>)
    expect(screen.getByRole('heading', { level: 3, name: 'Scan effectiveness' })).toBeInTheDocument()
  })
})
