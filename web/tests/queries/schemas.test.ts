import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { server } from '@tests/mock/server'
import { http, HttpResponse } from 'msw'
import { useTableSnapshots } from '@/queries/schemas'
import { createWrapper } from '@tests/utils'

describe('useTableSnapshots()', () => {
  it('loads newest-first snapshot history for a written table', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(
      () => useTableSnapshots('acme-analytics', 'raw', 'events'),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const snaps = result.current.data!
    expect(snaps.length).toBeGreaterThan(0)
    // Current snapshot is first (reverse-chronological).
    expect(snaps[0].is_current).toBe(true)
    expect(snaps[snaps.length - 1].is_current).toBe(false)

    queryClient.clear()
  })

  it('returns an empty history for a table never written to', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(
      // The `users` fixture has snapshot_id === null.
      () => useTableSnapshots('acme-analytics', 'raw', 'users'),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toEqual([])

    queryClient.clear()
  })

  it('surfaces a 404 for an unknown table', async () => {
    server.use(
      http.get(
        '/api/workspaces/:ws/schemas/:schema/tables/:table/snapshots',
        () => HttpResponse.json({ detail: 'Table not found' }, { status: 404 }),
      ),
    )
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(
      () => useTableSnapshots('acme-analytics', 'raw', 'ghost'),
      { wrapper },
    )

    await waitFor(() => expect(result.current.isError).toBe(true))
    queryClient.clear()
  })
})
