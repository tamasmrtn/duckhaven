import { describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { server } from '@tests/mock/server'
import { http, HttpResponse } from 'msw'
import { useQuery_, useDispatchQuery, useSavedQueries, useSaveQuery } from '@/queries/queries'
import { SAVED_QUERIES } from '@/mock/fixtures/queries'
import { createWrapper } from '@tests/utils'

describe('useQuery_()', () => {
  it('returns null data when id is null', () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useQuery_(null), { wrapper })
    expect(result.current.data).toBeUndefined()
    queryClient.clear()
  })

  it('fetches query data by id', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useQuery_('q-1'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.id).toBe('q-1')
    expect(result.current.data?.status).toBe('done')
    queryClient.clear()
  })

  it('uses 300ms refetchInterval when status is queued', async () => {
    server.use(
      http.get('/api/queries/q-running', () =>
        HttpResponse.json({ id: 'q-running', status: 'queued', row_count: null, duration_ms: null, error: null }),
      ),
    )
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useQuery_('q-running'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const query = queryClient.getQueryCache().find({ queryKey: ['query', 'q-running'] })
    const interval = typeof query?.options.refetchInterval === 'function'
      ? query.options.refetchInterval(query as never)
      : query?.options.refetchInterval
    expect(interval).toBe(300)
    queryClient.clear()
  })

  it('stops refetching when status is done', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useQuery_('q-1'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const query = queryClient.getQueryCache().find({ queryKey: ['query', 'q-1'] })
    const interval = typeof query?.options.refetchInterval === 'function'
      ? query.options.refetchInterval(query as never)
      : query?.options.refetchInterval
    expect(interval).toBe(false)
    queryClient.clear()
  })
})

describe('useDispatchQuery()', () => {
  it('returns queued status on dispatch', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useDispatchQuery('acme-analytics'), { wrapper })
    const response = await result.current.mutateAsync({
      sql: 'SELECT 1',
      agentId: 'ag-1',
    })
    expect(response.status).toBe('queued')
    expect(typeof response.id).toBe('string')
    queryClient.clear()
  })
})

describe('useSavedQueries()', () => {
  it('returns saved queries for the given workspace', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useSavedQueries('acme-analytics'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const ws1Queries = SAVED_QUERIES.filter((q) => q.workspace_id === 'ws-1')
    expect(result.current.data).toHaveLength(ws1Queries.length)
    queryClient.clear()
  })
})

describe('useSaveQuery()', () => {
  it('saves a query and invalidates the saved-queries cache', async () => {
    const { queryClient, wrapper } = createWrapper()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useSaveQuery('acme-analytics'), { wrapper })
    await result.current.mutateAsync({ name: 'My query', sql: 'SELECT 1' })

    expect(invalidateSpy).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['workspace', 'acme-analytics', 'saved-queries'] }),
    )
    queryClient.clear()
  })
})
