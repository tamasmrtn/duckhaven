import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { ResultsTable } from '@/features/worksheet/ResultsTable'

const rows = Array.from({ length: 50 }, (_, i) => ({ n: i }))

function getScroller(container: HTMLElement): HTMLElement {
  return container.querySelector('.overflow-auto') as HTMLElement
}

function setScroll(el: HTMLElement, top: number) {
  Object.defineProperty(el, 'scrollHeight', { value: 1000, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: 300, configurable: true })
  Object.defineProperty(el, 'scrollTop', { value: top, configurable: true })
}

describe('ResultsTable infinite scroll', () => {
  it('calls onLoadMore when scrolled near the bottom', () => {
    const onLoadMore = vi.fn()
    const { container } = render(
      <ResultsTable
        columns={['n']}
        rows={rows}
        total={120}
        hasMore
        onLoadMore={onLoadMore}
      />,
    )
    const scroller = getScroller(container)
    setScroll(scroller, 750) // 1000 - 750 - 300 = -50 < 200
    fireEvent.scroll(scroller)
    expect(onLoadMore).toHaveBeenCalledTimes(1)
  })

  it('does not call onLoadMore when far from the bottom', () => {
    const onLoadMore = vi.fn()
    const { container } = render(
      <ResultsTable
        columns={['n']}
        rows={rows}
        total={120}
        hasMore
        onLoadMore={onLoadMore}
      />,
    )
    const scroller = getScroller(container)
    setScroll(scroller, 0) // 1000 - 0 - 300 = 700 >= 200
    fireEvent.scroll(scroller)
    expect(onLoadMore).not.toHaveBeenCalled()
  })

  it('does not fetch when there are no more pages', () => {
    const onLoadMore = vi.fn()
    const { container } = render(
      <ResultsTable
        columns={['n']}
        rows={rows}
        total={50}
        hasMore={false}
        onLoadMore={onLoadMore}
      />,
    )
    const scroller = getScroller(container)
    setScroll(scroller, 900)
    fireEvent.scroll(scroller)
    expect(onLoadMore).not.toHaveBeenCalled()
  })

  it('does not fetch again while a page is already loading', () => {
    const onLoadMore = vi.fn()
    const { container } = render(
      <ResultsTable
        columns={['n']}
        rows={rows}
        total={120}
        hasMore
        isLoadingMore
        onLoadMore={onLoadMore}
      />,
    )
    const scroller = getScroller(container)
    setScroll(scroller, 900)
    fireEvent.scroll(scroller)
    expect(onLoadMore).not.toHaveBeenCalled()
  })
})

describe('ResultsTable error state', () => {
  const longError =
    'Binder Error: Referenced column "foo" not found\n' +
    'in table "orders". Candidate bindings: "bar", "baz"'

  it('renders the full, untruncated failure message in the results area', () => {
    render(<ResultsTable columns={[]} rows={[]} total={0} error={longError} />)
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Binder Error')
    expect(alert).toHaveTextContent('Candidate bindings')
    expect(screen.getByText('Query failed')).toBeInTheDocument()
  })

  it('shows the error instead of rows even when result rows are present', () => {
    render(
      <ResultsTable
        columns={['n']}
        rows={rows}
        total={50}
        error="boom"
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('boom')
    // The table (and its column header) must not render alongside the error.
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })
})

describe('ResultsTable column types', () => {
  it('shows each column type next to its header when columnSchema is passed', () => {
    render(
      <ResultsTable
        columns={['n']}
        columnSchema={[{ name: 'n', type: 'BIGINT' }]}
        rows={[{ n: 1 }]}
        total={1}
      />,
    )
    expect(screen.getByText('BIGINT')).toBeInTheDocument()
  })

  it('shows no type badge when columnSchema is omitted', () => {
    render(<ResultsTable columns={['n']} rows={[{ n: 1 }]} total={1} />)
    expect(screen.queryByText('BIGINT')).not.toBeInTheDocument()
  })
})

describe('ResultsTable sorting', () => {
  function bodyRowValues() {
    return screen
      .getAllByRole('row')
      .slice(1) // drop the header row
      .map((row) => row.textContent)
  }

  it('cycles a column through descending, ascending, and back to unsorted on repeated clicks', () => {
    render(
      <ResultsTable
        columns={['n']}
        rows={[{ n: 3 }, { n: 1 }, { n: 2 }]}
        total={3}
      />,
    )
    const header = screen.getByRole('button', { name: 'n' })
    expect(bodyRowValues()).toEqual(['3', '1', '2'])

    fireEvent.click(header)
    expect(bodyRowValues()).toEqual(['3', '2', '1'])

    fireEvent.click(header)
    expect(bodyRowValues()).toEqual(['1', '2', '3'])

    fireEvent.click(header)
    expect(bodyRowValues()).toEqual(['3', '1', '2'])
  })

  it('flags the row count as partial while a sort is active and not all rows are loaded', () => {
    render(
      <ResultsTable
        columns={['n']}
        rows={[{ n: 1 }, { n: 2 }]}
        total={5}
      />,
    )
    expect(screen.getByText('2 / 5 rows')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'n' }))
    expect(screen.getByText('Sorted: 2 of 5 loaded')).toBeInTheDocument()

    // Clearing the sort (third click) restores the plain count.
    fireEvent.click(screen.getByRole('button', { name: 'n' }))
    fireEvent.click(screen.getByRole('button', { name: 'n' }))
    expect(screen.getByText('2 / 5 rows')).toBeInTheDocument()
  })
})
