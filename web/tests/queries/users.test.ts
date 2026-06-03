import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { server } from '@tests/mock/server'
import { http, HttpResponse } from 'msw'
import { useAdminUsers } from '@/queries/users'
import { ALL_USERS } from '@/mock/fixtures/users'
import { createWrapper } from '@tests/utils'

describe('useAdminUsers()', () => {
  it('returns the registered users from the admin endpoint', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useAdminUsers(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toHaveLength(ALL_USERS.length)
    expect(result.current.data?.map((u) => u.email)).toContain(
      'marton@duckhaven.local',
    )
    queryClient.clear()
  })

  it('sets isError when the endpoint returns 401', async () => {
    server.use(
      http.get('/api/admin/users', () => new HttpResponse(null, { status: 401 })),
    )
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useAdminUsers(), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))
    queryClient.clear()
  })
})
