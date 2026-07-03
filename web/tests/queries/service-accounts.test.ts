import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useServiceAccounts } from '@/queries/service-accounts'
import { SERVICE_ACCOUNTS } from '@/mock/fixtures/service-accounts'
import { createWrapper } from '@tests/utils'

describe('useServiceAccounts()', () => {
  it('returns the service-account list', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useServiceAccounts(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(SERVICE_ACCOUNTS.length)
    queryClient.clear()
  })
})
