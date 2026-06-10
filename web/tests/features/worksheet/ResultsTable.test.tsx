import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
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
