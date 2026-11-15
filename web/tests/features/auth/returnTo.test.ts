import { afterEach, describe, expect, it } from 'vitest'
import { keepReturnTo, safeReturnTo, takeReturnTo } from '@/features/auth/returnTo'

afterEach(() => sessionStorage.clear())

describe('safeReturnTo', () => {
  it.each([
    ['/acme-analytics/dashboards/sales?p.region=EU#revenue', '/acme-analytics/dashboards/sales?p.region=EU#revenue'],
    ['/acme-analytics/worksheets', '/acme-analytics/worksheets'],
  ])('keeps the page %s', (value, path) => {
    expect(safeReturnTo(value)).toBe(path)
  })

  it.each([
    '//evil.example/x',
    '/\\evil.example/x',
    'https://evil.example/x',
    'javascript:alert(1)',
    'acme-analytics/worksheets',
    '',
    undefined,
    42,
  ])('refuses %s', (value) => {
    expect(safeReturnTo(value)).toBeUndefined()
  })
})

describe('the kept page', () => {
  it('is read once', () => {
    keepReturnTo('/acme-analytics/dashboards')

    expect(takeReturnTo()).toBe('/acme-analytics/dashboards')
    expect(takeReturnTo()).toBeUndefined()
  })

  it('is refused when it points at another site', () => {
    sessionStorage.setItem('dh-return-to', '//evil.example/x')

    expect(takeReturnTo()).toBeUndefined()
  })
})
