import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useAgents } from '@/queries/agents'
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
