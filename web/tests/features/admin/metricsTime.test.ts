import { describe, it, expect } from 'vitest'
import {
  formatRelativeTick,
  formatAbsoluteTimestamp,
  relativeMinuteTicks,
} from '@/features/admin/metricsTime'

const at = (iso: string) => Date.parse(iso)

describe('formatRelativeTick', () => {
  const ref = at('2026-06-08T14:32:05Z')

  it('labels the latest sample as "now"', () => {
    expect(formatRelativeTick(at('2026-06-08T14:32:05Z'), ref)).toBe('now')
  })

  it('shows seconds within the first minute', () => {
    expect(formatRelativeTick(at('2026-06-08T14:31:35Z'), ref)).toBe('-30s')
  })

  it('switches to minutes at the 60s boundary', () => {
    expect(formatRelativeTick(at('2026-06-08T14:31:05Z'), ref)).toBe('-1m')
  })

  it('rounds longer gaps to whole minutes', () => {
    expect(formatRelativeTick(at('2026-06-08T14:30:00Z'), ref)).toBe('-2m')
  })
})

describe('relativeMinuteTicks', () => {
  const max = at('2026-06-08T14:32:05Z')

  it('places one ascending tick per minute, ending at the latest sample', () => {
    const min = at('2026-06-08T14:27:30Z')
    const ticks = relativeMinuteTicks(min, max)
    // 14:32:05 back to 14:28:05 (one more step would fall before min).
    expect(ticks).toEqual([
      at('2026-06-08T14:28:05Z'),
      at('2026-06-08T14:29:05Z'),
      at('2026-06-08T14:30:05Z'),
      at('2026-06-08T14:31:05Z'),
      max,
    ])
    // Rendered labels are distinct — the bug was repeated "-2m -2m -2m".
    const labels = ticks.map((t) => formatRelativeTick(t, max))
    expect(labels).toEqual(['-4m', '-3m', '-2m', '-1m', 'now'])
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('returns a single "now" tick for a sub-minute window', () => {
    const ticks = relativeMinuteTicks(at('2026-06-08T14:31:45Z'), max)
    expect(ticks).toEqual([max])
  })
})

describe('formatAbsoluteTimestamp', () => {
  it('includes the date, time-of-day, and a time-zone token', () => {
    const out = formatAbsoluteTimestamp(at('2026-06-08T14:32:05Z'))
    expect(out).toMatch(/Jun/)
    expect(out).toMatch(/8/)
    // HH:MM:SS present (locale may use 12/24h, so just assert the seconds field).
    expect(out).toMatch(/\d{1,2}:\d{2}:\d{2}/)
    // A short TZ name (e.g. UTC, GMT+2, PDT) is appended.
    expect(out).toMatch(/[A-Z]{2,}|GMT/)
  })
})
