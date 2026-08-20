import { describe, it, expect, afterEach } from 'vitest'
import {
  recordRecentlyViewed,
  getRecentlyViewed,
} from '@/utils/recentlyViewed'

describe('recentlyViewed', () => {
  afterEach(() => {
    localStorage.clear()
  })

  it('returns an empty list when nothing has been recorded', () => {
    expect(getRecentlyViewed('acme')).toEqual([])
  })

  it('records an entry and puts it first', () => {
    recordRecentlyViewed('acme', {
      type: 'table',
      catalog: 'main',
      schema: 'public',
      name: 'orders',
    })
    const entries = getRecentlyViewed('acme')
    expect(entries).toHaveLength(1)
    expect(entries[0]).toMatchObject({
      type: 'table',
      catalog: 'main',
      schema: 'public',
      name: 'orders',
    })
  })

  it('dedupes by (type, catalog, schema, name) and moves the entry to the front', () => {
    recordRecentlyViewed('acme', {
      type: 'table',
      catalog: 'main',
      schema: 'public',
      name: 'orders',
    })
    recordRecentlyViewed('acme', {
      type: 'table',
      catalog: 'main',
      schema: 'public',
      name: 'customers',
    })
    recordRecentlyViewed('acme', {
      type: 'table',
      catalog: 'main',
      schema: 'public',
      name: 'orders',
    })

    const entries = getRecentlyViewed('acme')
    expect(entries).toHaveLength(2)
    expect(entries[0].name).toBe('orders')
    expect(entries[1].name).toBe('customers')
  })

  it('caps at 8 entries, dropping the oldest', () => {
    for (let i = 0; i < 10; i++) {
      recordRecentlyViewed('acme', {
        type: 'table',
        catalog: 'main',
        schema: 'public',
        name: `t${i}`,
      })
    }
    const entries = getRecentlyViewed('acme')
    expect(entries).toHaveLength(8)
    expect(entries[0].name).toBe('t9')
    expect(entries.map((e) => e.name)).not.toContain('t0')
    expect(entries.map((e) => e.name)).not.toContain('t1')
  })

  it('keeps entries scoped per workspace', () => {
    recordRecentlyViewed('acme', {
      type: 'table',
      catalog: 'main',
      schema: 'public',
      name: 'orders',
    })
    expect(getRecentlyViewed('other')).toEqual([])
  })
})
