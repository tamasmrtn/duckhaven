import { describe, it, expect } from 'vitest'
import { monitoringRefetchInterval } from '@/queries/agents'

describe('monitoringRefetchInterval()', () => {
  const now = Date.parse('2026-09-30T12:00:00Z')

  it('follows a live range closely and a multi-day one lazily', () => {
    expect(monitoringRefetchInterval({ window: '1h' }, now)).toBe(15_000)
    expect(monitoringRefetchInterval({ window: '24h' }, now)).toBe(15_000)
    expect(monitoringRefetchInterval({ window: '7d' }, now)).toBe(60_000)
  })

  it('never polls a zoomed range that ends in the past', () => {
    expect(
      monitoringRefetchInterval(
        { start: '2026-09-30T09:00:00Z', end: '2026-09-30T10:00:00Z' },
        now,
      ),
    ).toBe(false)
    expect(
      monitoringRefetchInterval(
        { start: '2026-09-30T11:00:00Z', end: '2026-09-30T12:00:00Z' },
        now,
      ),
    ).toBe(15_000)
  })
})
