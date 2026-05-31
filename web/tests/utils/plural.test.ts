import { describe, it, expect } from 'vitest'
import { plural } from '@/utils'

describe('plural', () => {
  it('uses the singular form for exactly one', () => {
    expect(plural(1, 'backend')).toBe('1 backend')
    expect(plural(1, 'user')).toBe('1 user')
  })

  it('uses the plural form for zero and many', () => {
    expect(plural(0, 'agent')).toBe('0 agents')
    expect(plural(3, 'user')).toBe('3 users')
  })

  it('supports a custom suffix', () => {
    expect(plural(2, 'box', 'es')).toBe('2 boxes')
    expect(plural(1, 'box', 'es')).toBe('1 box')
  })
})
