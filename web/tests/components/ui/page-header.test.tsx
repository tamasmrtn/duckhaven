import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PageHeader } from '@/components/ui/page-header'

describe('PageHeader', () => {
  it('renders a title-only header', () => {
    render(<PageHeader title="Settings" />)
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument()
  })

  it('renders actions inline with the title', () => {
    render(<PageHeader title="History" actions={<button>Refresh</button>} />)
    expect(screen.getByRole('heading', { name: 'History' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument()
  })

  it('renders a leading element and a badge alongside the title', () => {
    render(
      <PageHeader
        title="Connection"
        leading={<button>Back</button>}
        badge={<span>live</span>}
      />,
    )
    expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Connection' })).toBeInTheDocument()
    expect(screen.getByText('live')).toBeInTheDocument()
  })

  it('renders breadcrumb, badge, description and actions together', () => {
    render(
      <PageHeader
        breadcrumb={<nav aria-label="breadcrumb">acme › models</nav>}
        title="Revenue"
        badge={<span>published</span>}
        description="What revenue means, and how it is calculated."
        actions={<button>Publish</button>}
      />,
    )
    expect(screen.getByRole('navigation', { name: 'breadcrumb' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Revenue' })).toBeInTheDocument()
    expect(screen.getByText('published')).toBeInTheDocument()
    expect(
      screen.getByText('What revenue means, and how it is calculated.'),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Publish' })).toBeInTheDocument()
  })

  it('renders a secondary row below the title row', () => {
    render(
      <PageHeader
        title="Admin"
        secondaryRow={<nav aria-label="Admin sections">sections</nav>}
      />,
    )
    expect(
      screen.getByRole('navigation', { name: 'Admin sections' }),
    ).toBeInTheDocument()
  })

  it('omits the description paragraph when none is given', () => {
    const { container } = render(<PageHeader title="Catalog" />)
    expect(container.querySelector('p')).not.toBeInTheDocument()
  })
})
