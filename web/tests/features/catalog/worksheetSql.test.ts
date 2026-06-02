import { describe, it, expect, beforeEach } from 'vitest'
import {
  alterTemplate,
  selectTemplate,
  stashWorksheetSql,
  takePendingSql,
} from '@/features/catalog/worksheetSql'

describe('worksheetSql', () => {
  beforeEach(() => window.localStorage.clear())

  it('builds quoted SELECT and ALTER templates', () => {
    expect(selectTemplate('analytics', 'events')).toBe(
      'SELECT * FROM "analytics"."events" LIMIT 100;',
    )
    expect(alterTemplate('analytics', 'events')).toBe(
      'ALTER TABLE "analytics"."events" ADD COLUMN new_column VARCHAR;',
    )
  })

  it('stash then take returns the SQL once and clears it', () => {
    stashWorksheetSql('ws1', 'SELECT 1;')
    expect(takePendingSql('ws1')).toBe('SELECT 1;')
    expect(takePendingSql('ws1')).toBeNull()
  })

  it('scopes pending SQL per workspace', () => {
    stashWorksheetSql('ws1', 'SELECT 1;')
    expect(takePendingSql('ws2')).toBeNull()
    expect(takePendingSql('ws1')).toBe('SELECT 1;')
  })
})
