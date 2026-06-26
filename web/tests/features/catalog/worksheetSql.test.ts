import { describe, it, expect, beforeEach } from 'vitest'
import {
  alterTemplate,
  selectTemplate,
  snapshotByTimestampTemplate,
  snapshotByVersionTemplate,
  stashWorksheetSql,
  stashWorksheetQuery,
  takePendingQuery,
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

  it('builds Iceberg time-travel templates (AT VERSION / TIMESTAMP)', () => {
    expect(
      snapshotByVersionTemplate('analytics', 'events', '7264354987654321234'),
    ).toBe(
      'SELECT * FROM "analytics"."events" AT (VERSION => 7264354987654321234) LIMIT 100;',
    )
    expect(
      snapshotByTimestampTemplate('analytics', 'events', '2026-05-15T14:03:00Z'),
    ).toBe(
      "SELECT * FROM \"analytics\".\"events\" AT (TIMESTAMP => '2026-05-15T14:03:00Z') LIMIT 100;",
    )
  })

  it('stash then take returns the SQL once and clears it', () => {
    stashWorksheetSql('ws1', 'SELECT 1;')
    expect(takePendingQuery('ws1')).toEqual({ sql: 'SELECT 1;' })
    expect(takePendingQuery('ws1')).toBeNull()
  })

  it('scopes pending SQL per workspace', () => {
    stashWorksheetSql('ws1', 'SELECT 1;')
    expect(takePendingQuery('ws2')).toBeNull()
    expect(takePendingQuery('ws1')).toEqual({ sql: 'SELECT 1;' })
  })

  it('carries the agent and saved query id for a saved-query hand-off', () => {
    stashWorksheetQuery('ws1', {
      sql: 'SELECT 2;',
      agentId: 'ag-1',
      savedQueryId: 'sq-1',
    })
    expect(takePendingQuery('ws1')).toEqual({
      sql: 'SELECT 2;',
      agentId: 'ag-1',
      savedQueryId: 'sq-1',
    })
  })
})
