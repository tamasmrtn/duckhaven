import { describe, it, expect } from 'vitest'
import { splitStatements, activeStatement } from '@/features/worksheet/statements'

describe('splitStatements', () => {
  it('splits multiple statements and strips trailing semicolons', () => {
    expect(splitStatements('SELECT 1; SELECT 2;')).toEqual([
      'SELECT 1',
      'SELECT 2',
    ])
  })

  it('handles a single statement with no trailing semicolon', () => {
    expect(splitStatements('SELECT 1')).toEqual(['SELECT 1'])
  })

  it('drops blank / whitespace-only segments', () => {
    expect(splitStatements('SELECT 1;;  ;SELECT 2;  ')).toEqual([
      'SELECT 1',
      'SELECT 2',
    ])
  })

  it('ignores semicolons inside single-quoted strings, incl. escapes', () => {
    expect(splitStatements("SELECT 'a;b''c;d' AS x; SELECT 2")).toEqual([
      "SELECT 'a;b''c;d' AS x",
      'SELECT 2',
    ])
  })

  it('ignores semicolons inside double-quoted identifiers', () => {
    expect(splitStatements('SELECT 1 AS "a;b"; SELECT 2')).toEqual([
      'SELECT 1 AS "a;b"',
      'SELECT 2',
    ])
  })

  it('ignores semicolons inside line comments', () => {
    expect(splitStatements('SELECT 1 -- a;b\n; SELECT 2')).toEqual([
      'SELECT 1 -- a;b',
      'SELECT 2',
    ])
  })

  it('ignores semicolons inside block comments', () => {
    expect(splitStatements('SELECT /* a;b */ 1; SELECT 2')).toEqual([
      'SELECT /* a;b */ 1',
      'SELECT 2',
    ])
  })

  it('returns an empty array for blank input', () => {
    expect(splitStatements('   \n  ')).toEqual([])
  })
})

describe('activeStatement', () => {
  const sql = 'SELECT 1;\nSELECT 2;\nSELECT 3'

  it('returns the statement at the cursor (first)', () => {
    expect(activeStatement(sql, 3)).toBe('SELECT 1')
  })

  it('returns the statement at the cursor (middle)', () => {
    expect(activeStatement(sql, 13)).toBe('SELECT 2')
  })

  it('returns the statement at the cursor (last)', () => {
    expect(activeStatement(sql, sql.length - 1)).toBe('SELECT 3')
  })

  it('resolves a cursor just after a semicolon to the next statement', () => {
    // index 9 is the newline immediately after the first `;`.
    expect(activeStatement(sql, 9)).toBe('SELECT 2')
  })

  it('returns the statement when there is no trailing semicolon', () => {
    expect(activeStatement('SELECT 42', 4)).toBe('SELECT 42')
  })

  it('returns empty for a cursor in trailing whitespace', () => {
    expect(activeStatement('SELECT 1;   ', 11)).toBe('')
  })

  it('clamps an out-of-range offset', () => {
    expect(activeStatement('SELECT 1', 999)).toBe('SELECT 1')
    expect(activeStatement('SELECT 1', -5)).toBe('SELECT 1')
  })

  it('does not split on a semicolon inside a string', () => {
    expect(activeStatement("SELECT 'a;b'", 4)).toBe("SELECT 'a;b'")
  })
})
