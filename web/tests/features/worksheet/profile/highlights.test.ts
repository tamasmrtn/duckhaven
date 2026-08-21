import { describe, it, expect } from 'vitest'
import {
  isBadEstimate,
  isRowsReadCorrected,
  isScanBlowUp,
  isSpilled,
  isTimeHotspot,
  nodeBadges,
  rowsReadByScan,
} from '@/features/worksheet/profile/highlights'
import type { QueryProfileNode, QueryProfileSummary } from '@/types/query'

function node(over: Partial<QueryProfileNode> = {}): QueryProfileNode {
  return {
    type: 'TABLE_SCAN',
    name: 'ICEBERG_SCAN',
    estimated_cardinality: null,
    rows_scanned: null,
    rows_produced: null,
    time_ms: null,
    result_bytes: null,
    extra_info: {},
    children: [],
    ...over,
  }
}

function summary(over: Partial<QueryProfileSummary> = {}): QueryProfileSummary {
  return {
    latency_ms: 1000,
    cpu_time_ms: 1000,
    rows_returned: 100,
    result_bytes: 0,
    peak_memory_bytes: 0,
    spill_bytes: 0,
    bytes_read: 0,
    bytes_written: 0,
    ...over,
  }
}

describe('rowsReadByScan', () => {
  it('leaves a native scan alone — only file readers double count', () => {
    // Measured on 1.5.5: a native SEQ_SCAN reports 200,000 at 1, 2, 4 and 8
    // threads, while a PARQUET_SCAN of the same relation reports rows x
    // threads. Dividing the native figure would understate it eightfold and
    // present that as a correction.
    const native = node({
      name: 'SEQ_SCAN',
      rows_scanned: 200_000,
      extra_info: { Table: 'memory.main.orders', Type: 'Sequential Scan' },
    })
    expect(rowsReadByScan(native, summary({ reserved_threads: 8 }))).toBe(200_000)
    expect(isRowsReadCorrected(native, summary({ reserved_threads: 8 }))).toBe(false)
  })

  it('divides out the per-thread double count', () => {
    // DuckDB counts the whole relation once per participating thread, so a
    // 200k-row file read on 8 threads is reported as 1.6M.
    // A file reader: no Table key, so the figure is rows x threads.
    const n = node({ rows_scanned: 1_600_000 })
    expect(rowsReadByScan(n, summary({ reserved_threads: 8 }))).toBe(200_000)
    expect(rowsReadByScan(n, summary({ reserved_threads: 1 }))).toBe(1_600_000)
  })

  it('leaves the figure alone when there is no thread count to divide by', () => {
    // Profiles captured before reserved_threads was recorded.
    const n = node({ rows_scanned: 1_600_000 })
    expect(rowsReadByScan(n, summary())).toBe(1_600_000)
  })

  it('rounds rather than showing a fractional row count', () => {
    const n = node({ rows_scanned: 1_000_000 })
    expect(Number.isInteger(rowsReadByScan(n, summary({ reserved_threads: 3 })))).toBe(
      true,
    )
  })

  it('is zero for an operator that reports no scan', () => {
    expect(rowsReadByScan(node(), summary({ reserved_threads: 4 }))).toBe(0)
  })

  it('reports whether the figure was corrected, so the UI can explain it', () => {
    const fileScan = node({ rows_scanned: 1_000 })
    expect(isRowsReadCorrected(fileScan, summary({ reserved_threads: 4 }))).toBe(true)
    expect(isRowsReadCorrected(fileScan, summary({ reserved_threads: 1 }))).toBe(false)
    expect(isRowsReadCorrected(fileScan, summary())).toBe(false)
  })
})

describe('isScanBlowUp', () => {
  it('flags a scan that read far more rows than it emitted', () => {
    // 1M read down to 500 — a filter that never reached the reader.
    const n = node({ rows_scanned: 1_000_000, rows_produced: 500 })
    expect(isScanBlowUp(n, summary({ reserved_threads: 1 }))).toBe(true)
  })

  it('does not flag a scan that emitted what it read', () => {
    const n = node({ rows_scanned: 1_000_000, rows_produced: 1_000_000 })
    expect(isScanBlowUp(n, summary({ reserved_threads: 1 }))).toBe(false)
  })

  it('judges the scan by its own output, not the query result', () => {
    // The case that made the old spelling fire on 84% of real queries: a full
    // scan feeding an aggregate that returns a handful of rows. The scan wasted
    // nothing; the GROUP BY did the reducing.
    const n = node({ rows_scanned: 600_000_000, rows_produced: 600_000_000 })
    expect(isScanBlowUp(n, summary({ rows_returned: 5, reserved_threads: 1 }))).toBe(
      false,
    )
  })

  it('is not fooled by the per-thread double count', () => {
    // DuckDB reports rows × threads. This scan really read 3M rows to emit 1M —
    // a 3x ratio, well inside normal — but on an 8-thread reservation it is
    // *reported* as 24M, which against 1M produced reads as a 24x blow-up that
    // never happened. This is the shape of the 1,229 false positives measured
    // on real profiles.
    const n = node({ rows_scanned: 24_000_000, rows_produced: 1_000_000 })
    expect(isScanBlowUp(n, summary({ reserved_threads: 8 }))).toBe(false)
    // Without a thread count there is nothing to correct by, so the inflated
    // figure is taken at face value and the false positive stands. Profiles
    // captured before reserved_threads was recorded behave the old way.
    expect(isScanBlowUp(n, summary())).toBe(true)
  })

  it('does not shift the threshold for a native scan', () => {
    // 1M read to emit 1M: no waste. Dividing by 8 would make it look like
    // 125k -> 1M, and the badge must not fire either way.
    const native = node({
      name: 'SEQ_SCAN',
      rows_scanned: 1_000_000,
      rows_produced: 1_000_000,
      extra_info: { Table: 'memory.main.orders' },
    })
    expect(isScanBlowUp(native, summary({ reserved_threads: 8 }))).toBe(false)
  })

  it('still flags a real blow-up on a many-threaded reservation', () => {
    // Really read 1M (8M reported over 8 threads) to emit 10.
    const n = node({ rows_scanned: 8_000_000, rows_produced: 10 })
    expect(isScanBlowUp(n, summary({ reserved_threads: 8 }))).toBe(true)
  })

  it('flags a scan that read plenty and emitted nothing', () => {
    const n = node({ rows_scanned: 1_000_000, rows_produced: 0 })
    expect(isScanBlowUp(n, summary({ reserved_threads: 1 }))).toBe(true)
  })

  it('ignores small scans and nodes with no row counts', () => {
    // Under the floor: a 9k-row scan is not worth a badge whatever the ratio.
    expect(
      isScanBlowUp(node({ rows_scanned: 9_000, rows_produced: 1 }), summary()),
    ).toBe(false)
    // Operators that report no output cannot be judged.
    expect(
      isScanBlowUp(node({ rows_scanned: 1_000_000, rows_produced: null }), summary()),
    ).toBe(false)
    expect(isScanBlowUp(node(), summary())).toBe(false)
  })
})

describe('isSpilled', () => {
  it('is a query-level signal', () => {
    expect(isSpilled(summary({ spill_bytes: 1 }))).toBe(true)
    expect(isSpilled(summary({ spill_bytes: 0 }))).toBe(false)
  })
})

describe('isBadEstimate', () => {
  it('flags an actual cardinality far from the estimate, in either direction', () => {
    expect(
      isBadEstimate(node({ rows_produced: 100_000, estimated_cardinality: 100 })),
    ).toBe(true)
    expect(
      isBadEstimate(node({ rows_produced: 100, estimated_cardinality: 100_000 })),
    ).toBe(true)
  })

  it('ignores small numbers and missing values', () => {
    // Both under the floor: being 10x wrong about 50 rows costs nothing.
    expect(
      isBadEstimate(node({ rows_produced: 500, estimated_cardinality: 5 })),
    ).toBe(false)
    expect(
      isBadEstimate(node({ rows_produced: null, estimated_cardinality: 100 })),
    ).toBe(false)
  })
})

describe('isTimeHotspot', () => {
  it('flags an operator dominating the query', () => {
    expect(isTimeHotspot(node({ time_ms: 400 }), summary({ latency_ms: 1000 }))).toBe(
      true,
    )
    expect(isTimeHotspot(node({ time_ms: 200 }), summary({ latency_ms: 1000 }))).toBe(
      false,
    )
  })

  it('is safe when there is no timing to compare', () => {
    expect(isTimeHotspot(node({ time_ms: null }), summary())).toBe(false)
    expect(isTimeHotspot(node({ time_ms: 10 }), summary({ latency_ms: 0 }))).toBe(false)
  })
})

describe('nodeBadges', () => {
  it('collects every badge a node earns', () => {
    const n = node({
      rows_scanned: 1_000_000,
      rows_produced: 10,
      estimated_cardinality: 100_000,
      time_ms: 900,
    })
    expect(nodeBadges(n, summary({ latency_ms: 1000, reserved_threads: 1 }))).toEqual([
      'scan',
      'estimate',
      'time',
    ])
  })

  it('is empty for an unremarkable operator', () => {
    expect(nodeBadges(node({ rows_produced: 10, time_ms: 1 }), summary())).toEqual([])
  })
})
