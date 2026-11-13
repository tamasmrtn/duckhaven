import { describe, it, expect } from 'vitest'
import { SERIES, resolve } from '@/components/charts/chartColors'

// These lock in properties that were established by running the palette
// validator (CVD separation, lightness bands, contrast against DuckHaven's own
// surfaces). The validator is not runnable from Vitest, so what is guarded here
// is that nobody edits a hex or reorders a slot without redoing that work.

const HEX = /^#[0-9a-f]{6}$/

describe('chart colors', () => {
  it('ships a separately chosen value for each mode', () => {
    // Dark is not an automatic flip of light — each column was validated against
    // its own surface.
    for (const color of SERIES) {
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

  it('resolves by theme', () => {
    expect(resolve(SERIES[0], false)).toBe(SERIES[0].light)
    expect(resolve(SERIES[0], true)).toBe(SERIES[0].dark)
  })
})
