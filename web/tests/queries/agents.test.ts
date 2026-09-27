import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { useAgents, useRuntimes } from '@/queries/agents'
import { useSqlMetadata } from '@/queries/sqlMetadata'
import { AGENTS } from '@/mock/fixtures/agents'
import { createWrapper } from '@tests/utils'

describe('useAgents()', () => {
  it('returns the full agent list', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useAgents(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(AGENTS.length)
    queryClient.clear()
  })

  it('has refetchInterval of 5000ms configured', () => {
    const { queryClient, wrapper } = createWrapper()
    renderHook(() => useAgents(), { wrapper })
    const query = queryClient.getQueryCache().find({ queryKey: ['agents'] })
    // The refetchInterval option is baked into the query options
    expect(query?.options.refetchInterval).toBe(5000)
    queryClient.clear()
  })
})

describe('useRuntimes()', () => {
  it('lists the curated runtimes with exactly one default', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useRuntimes(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.filter((r) => r.default)).toHaveLength(1)
    queryClient.clear()
  })
})

describe('useSqlMetadata()', () => {
  it('asks for the dictionary of the worksheet\'s own agent', async () => {
    const urls: string[] = []
    server.use(
      http.get('/api/workspaces/:ws/sql-metadata', ({ request }) => {
        urls.push(request.url)
        return HttpResponse.json({ functions: [], keywords: [], types: [] })
      }),
    )
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useSqlMetadata('acme-analytics', 'ag-2'), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(new URL(urls[0]).searchParams.get('agent_id')).toBe('ag-2')
    queryClient.clear()
  })
})
