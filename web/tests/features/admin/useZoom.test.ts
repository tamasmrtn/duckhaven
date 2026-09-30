import { describe, it, expect } from 'vitest'
import { IDLE, release, selection, zoomReducer } from '@/features/admin/monitoring/useZoom'
import { parseMonitoringSearch, rangeFromSearch } from '@/features/admin/monitoring/range'

const MIN = 60_000

describe('drag-to-zoom', () => {
  it('turns a drag into a range', () => {
    let s = zoomReducer(IDLE, { type: 'down', at: 0 })
    s = zoomReducer(s, { type: 'move', at: 30 * MIN })
    expect(selection(s)).toEqual([0, 30 * MIN])
    expect(release(s, null)).toEqual({ kind: 'zoom', from: 0, to: 30 * MIN })
  })

  it('zooms the same way dragging right to left', () => {
    let s = zoomReducer(IDLE, { type: 'down', at: 40 * MIN })
    s = zoomReducer(s, { type: 'move', at: 5 * MIN })
    expect(release(s, null)).toEqual({ kind: 'zoom', from: 5 * MIN, to: 40 * MIN })
  })

  it('treats a click, or a drag too short to zoom into, as selecting a bucket', () => {
    const click = zoomReducer(IDLE, { type: 'down', at: 7 * MIN })
    expect(selection(click)).toBeNull()
    expect(release(click, null)).toEqual({ kind: 'select', at: 7 * MIN })

    const wobble = zoomReducer(click, { type: 'move', at: 9 * MIN })
    expect(release(wobble, null)).toEqual({ kind: 'select', at: 7 * MIN })
  })

  it('ignores movement without a press, and releases to nothing when idle', () => {
    expect(zoomReducer(IDLE, { type: 'move', at: 5 })).toBe(IDLE)
    expect(release(IDLE, 5)).toEqual({ kind: 'none' })
    expect(zoomReducer(zoomReducer(IDLE, { type: 'down', at: 1 }), { type: 'cancel' })).toBe(IDLE)
  })
})

describe('monitoring range in the URL', () => {
  it('reads a preset, a zoomed range, or falls back to the default', () => {
    expect(rangeFromSearch(parseMonitoringSearch({ range: '7d' }))).toEqual({ window: '7d' })
    expect(
      rangeFromSearch(
        parseMonitoringSearch({ from: '2026-09-30T10:00:00Z', to: '2026-09-30T11:00:00Z' }),
      ),
    ).toEqual({ start: '2026-09-30T10:00:00Z', end: '2026-09-30T11:00:00Z' })
    expect(rangeFromSearch(parseMonitoringSearch({ range: '2w' }))).toEqual({ window: '8h' })
  })

  it('drops a zoomed range narrower than the minimum rather than erroring', () => {
    const search = parseMonitoringSearch({
      from: '2026-09-30T10:00:00Z',
      to: '2026-09-30T10:03:00Z',
    })
    expect(search).toEqual({})
  })
})
