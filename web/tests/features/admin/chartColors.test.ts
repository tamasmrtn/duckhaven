import { describe, it, expect } from 'vitest'
import {
  ACTIVITY,
  LOAD,
  OUTCOME,
  RESOURCE,
  SERIES,
  TIMELINE,
  resolve,
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

  it('draws every named group from the validated slots, never a new hue', () => {
    // LOAD, OUTCOME and RESOURCE were validated as picks from SERIES in the order
    // their charts stack them; a hex that is not a slot skipped that validation.
    const slots = new Set(SERIES)
    for (const group of [LOAD, OUTCOME, RESOURCE]) {
      for (const color of Object.values(group)) expect(slots.has(color)).toBe(true)
    }
  })

  it('keeps red off amber in the outcome stack', () => {
    // Adjacent failed/sql_error failed the normal-vision floor in dark mode.
    const order = Object.values(OUTCOME)
    const red = order.indexOf(SERIES[7])
    const amber = order.indexOf(SERIES[3])
    expect(Math.abs(red - amber)).toBeGreaterThan(1)
  })

  it('steps activity as one ordered ramp, not four unrelated hues', () => {
    // Idle -> busy is an ordered scale, so the order lives in the lightness where
    // a reader sees it without the legend. Separate hues failed outright: slate
    // "ready" against blue "query" came out below the normal-vision floor.
    const hue = (hex: string) => hex.slice(1, 3)
    expect(TIMELINE.busy).toBe(ACTIVITY.query)
    expect(TIMELINE.idle).toBe(ACTIVITY.ready)
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

  it('resolves by theme', () => {
    expect(resolve(LOAD.running, false)).toBe(LOAD.running.light)
    expect(resolve(LOAD.running, true)).toBe(LOAD.running.dark)
  })
})
