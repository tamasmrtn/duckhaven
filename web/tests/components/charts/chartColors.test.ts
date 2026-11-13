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
      '#05a26f',
      '#bd7f03',
      '#d76c95',
      '#008300',
      '#4a3aa7',
      '#e34948',
    ])
  })

  it('keeps every slot at 3:1 or more against the chart surface in both modes', () => {
    // WCAG 2 contrast, against `--bg-surface` in styles/tokens.css. Aqua, yellow
    // and magenta once sat at 2.69, 2.07 and 2.57 on the light surface.
    const luminance = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => {
        const c = parseInt(hex.slice(i, i + 2), 16) / 255
        return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    const contrast = (a: string, b: string) => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
      return (hi + 0.05) / (lo + 0.05)
    }
    for (const slot of SERIES) {
      expect(contrast(slot.light, '#f8fafc'), slot.light).toBeGreaterThanOrEqual(3)
      expect(contrast(slot.dark, '#111827'), slot.dark).toBeGreaterThanOrEqual(3)
    }
  })

  it('resolves by theme', () => {
    expect(resolve(SERIES[0], false)).toBe(SERIES[0].light)
    expect(resolve(SERIES[0], true)).toBe(SERIES[0].dark)
  })
})
