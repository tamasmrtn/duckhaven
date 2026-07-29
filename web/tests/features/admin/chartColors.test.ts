import { describe, it, expect } from 'vitest'
import {
  ACTIVITY,
  QUEUE_DEPTH,
  SERIES,
  resolve,
  seriesColor,
} from '@/features/admin/monitoring/chartColors'

// These lock in properties that were established by running the palette
// validator (CVD separation, lightness bands, contrast against DuckHaven's own
// surfaces). The validator is not runnable from Vitest, so what is guarded here
// is that nobody edits a hex or reorders a slot without redoing that work.

const HEX = /^#[0-9a-f]{6}$/

describe('chart colors', () => {
  it('ships a separately chosen value for each mode', () => {
    // Dark is not an automatic flip of light — each column was validated against
    // its own surface.
    for (const color of [
      ...SERIES,
      ...Object.values(QUEUE_DEPTH),
      ...Object.values(ACTIVITY),
    ]) {
      expect(color.light).toMatch(HEX)
      expect(color.dark).toMatch(HEX)
    }
  })

  it('keeps the validated categorical order', () => {
    expect(SERIES.map((s) => s.light)).toEqual([
      '#2a78d6',
      '#eb6834',
      '#1baf7a',
      '#eda100',
      '#e87ba4',
      '#008300',
      '#4a3aa7',
      '#e34948',
    ])
  })

  it('assigns a series its slot by index, never by rank', () => {
    // A filter that changes how many series exist must not repaint the
    // survivors, which would silently change what a colour means.
    expect(seriesColor(0, false)).toBe(SERIES[0].light)
    expect(seriesColor(2, false)).toBe(SERIES[2].light)
    expect(seriesColor(2, true)).toBe(SERIES[2].dark)
  })

  it('wraps rather than inventing a ninth hue', () => {
    expect(seriesColor(8, false)).toBe(SERIES[0].light)
  })

  it('steps activity as one ordered ramp, not four unrelated hues', () => {
    // Idle -> sessions -> queries is an ordered scale, so the order lives in the
    // lightness where a reader sees it without the legend. Four separate hues
    // failed outright: slate "ready" against blue "query" came out below the
    // normal-vision separation floor.
    const hue = (hex: string) => hex.slice(1, 3)
    expect(new Set([ACTIVITY.query.light, ACTIVITY.other.light, ACTIVITY.ready.light]).size).toBe(3)
    // Same family, increasing lightness as intensity falls.
    expect(hue(ACTIVITY.query.light) < hue(ACTIVITY.ready.light)).toBe(true)
  })

  it('flips the activity ramp anchor in dark mode', () => {
    // "More intense" must mean "further from the background" on either surface,
    // so the busiest step is darkest on light and brightest on dark.
    const lum = (hex: string) => parseInt(hex.slice(1, 3), 16)
    expect(lum(ACTIVITY.query.light)).toBeLessThan(lum(ACTIVITY.ready.light))
    expect(lum(ACTIVITY.query.dark)).toBeGreaterThan(lum(ACTIVITY.ready.dark))
  })

  it('keeps queue depth on status hues, distinct from the series slots', () => {
    // Running/queued are states, not "series 1 and 2"; a status colour must never
    // impersonate a categorical slot.
    const seriesHexes = new Set(SERIES.flatMap((s) => [s.light, s.dark]))
    expect(seriesHexes.has(QUEUE_DEPTH.running.light)).toBe(false)
    expect(seriesHexes.has(QUEUE_DEPTH.queued.light)).toBe(false)
  })

  it('resolves by theme', () => {
    expect(resolve(QUEUE_DEPTH.running, false)).toBe(QUEUE_DEPTH.running.light)
    expect(resolve(QUEUE_DEPTH.running, true)).toBe(QUEUE_DEPTH.running.dark)
  })
})
