import { describe, it, expect } from 'vitest'
import { objectPath } from '@/utils/objectPath'

describe('objectPath', () => {
  it('builds a schema route', () => {
    expect(objectPath('acme', { type: 'schema', catalog: 'prod', name: 'raw' })).toBe(
      '/acme/catalog/prod/raw',
    )
  })

  it('builds a table route', () => {
    expect(
      objectPath('acme', {
        type: 'table',
        catalog: 'prod',
        schema_name: 'raw',
        name: 'events',
      }),
    ).toBe('/acme/catalog/prod/raw/events')
  })

  it('percent-encodes each dynamic segment', () => {
    expect(
      objectPath('acme prod', {
        type: 'table',
        catalog: 'raw data',
        schema_name: 'a/b',
        name: 'events?',
      }),
    ).toBe('/acme%20prod/catalog/raw%20data/a%2Fb/events%3F')
  })
})
