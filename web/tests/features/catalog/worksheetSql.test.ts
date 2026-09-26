import { describe, it, expect } from 'vitest'
import {
  alterTemplate,
  selectTemplate,
  snapshotByTimestampTemplate,
  snapshotByVersionTemplate,
} from '@/features/catalog/worksheetSql'

describe('worksheetSql', () => {
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
})
