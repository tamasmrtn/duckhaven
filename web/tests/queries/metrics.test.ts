import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useAgentMetrics } from '@/queries/metrics'
import { METRICS } from '@/mock/fixtures/metrics'
import { createWrapper } from '@tests/utils'

describe('useAgentMetrics()', () => {
  it('returns the per-agent metrics windows', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useAgentMetrics(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(METRICS.length)
    expect(result.current.data?.[0].samples.length).toBeGreaterThan(0)
    queryClient.clear()
  })

  it('polls every 2000ms for live updates', () => {
    const { queryClient, wrapper } = createWrapper()
    renderHook(() => useAgentMetrics(), { wrapper })
    const query = queryClient
      .getQueryCache()
      .find({ queryKey: ['admin', 'metrics'] })
    expect(query?.options.refetchInterval).toBe(2000)
    queryClient.clear()
  })
})
