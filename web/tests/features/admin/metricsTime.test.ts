import { describe, it, expect } from 'vitest'
import {
  formatRelativeTick,
  formatAbsoluteTimestamp,
} from '@/features/admin/metricsTime'

describe('formatRelativeTick', () => {
  const ref = Date.parse('2026-06-08T14:32:05Z')

  it('labels the latest sample as "now"', () => {
    expect(formatRelativeTick('2026-06-08T14:32:05Z', ref)).toBe('now')
  })

  it('shows seconds within the first minute', () => {
    expect(formatRelativeTick('2026-06-08T14:31:35Z', ref)).toBe('-30s')
  })

  it('switches to minutes at the 60s boundary', () => {
    expect(formatRelativeTick('2026-06-08T14:31:05Z', ref)).toBe('-1m')
  })

  it('rounds longer gaps to whole minutes', () => {
    expect(formatRelativeTick('2026-06-08T14:30:00Z', ref)).toBe('-2m')
  })
})

describe('formatAbsoluteTimestamp', () => {
  it('includes the date, time-of-day, and a time-zone token', () => {
    const out = formatAbsoluteTimestamp('2026-06-08T14:32:05Z')
    expect(out).toMatch(/Jun/)
    expect(out).toMatch(/8/)
    // HH:MM:SS present (locale may use 12/24h, so just assert the seconds field).
    expect(out).toMatch(/\d{1,2}:\d{2}:\d{2}/)
    // A short TZ name (e.g. UTC, GMT+2, PDT) is appended.
    expect(out).toMatch(/[A-Z]{2,}|GMT/)
  })
})
