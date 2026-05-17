import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { server } from '@tests/mock/server'
import { http, HttpResponse } from 'msw'
import { useMe, useLogin, useLogout } from '@/queries/auth'
import { CURRENT_USER } from '@/mock/fixtures/users'
import { createWrapper } from '@tests/utils'

describe('useMe()', () => {
  it('returns the current user', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useMe(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toMatchObject({ email: CURRENT_USER.email })
    queryClient.clear()
  })

  it('sets isError when server returns 401', async () => {
    server.use(http.get('/api/me', () => new HttpResponse(null, { status: 401 })))
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useMe(), { wrapper })
    await waitFor(() => expect(result.current.isError).toBe(true))
    queryClient.clear()
  })
})

describe('useLogin()', () => {
  it('sets user in query cache on success', async () => {
    const { queryClient, wrapper } = createWrapper()
    const { result } = renderHook(() => useLogin(), { wrapper })
    await result.current.mutateAsync({ email: 'test@example.com', password: 'pass' })
    expect(queryClient.getQueryData(['me'])).toMatchObject({ email: CURRENT_USER.email })
    queryClient.clear()
  })
})

describe('useLogout()', () => {
  it('clears the query cache on success', async () => {
    const { queryClient, wrapper } = createWrapper()
    queryClient.setQueryData(['me'], CURRENT_USER)
    expect(queryClient.getQueryData(['me'])).toBeDefined()

    const { result } = renderHook(() => useLogout(), { wrapper })
    await result.current.mutateAsync()
    expect(queryClient.getQueryData(['me'])).toBeUndefined()
    queryClient.clear()
  })
})
