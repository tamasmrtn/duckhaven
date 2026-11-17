import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse, delay } from 'msw'
import type { ReactNode } from 'react'
import { server } from '@tests/mock/server'
import { createWrapper } from '@tests/utils'
import { ThemeProvider, useTheme } from '@/hooks/useTheme'
import { CURRENT_USER, currentUser } from '@/mock/fixtures/users'
import { THEME_CACHE_KEY } from '@/queries/auth'

function renderTheme<T>(hook: () => T = useTheme as () => T) {
  const { wrapper: Base } = createWrapper()
  const wrapper = ({ children }: { children: ReactNode }) => (
    <Base>
      <ThemeProvider>{children}</ThemeProvider>
    </Base>
  )
  return renderHook(hook, { wrapper })
}

/** Hold `/me` back, so a test can look at the first paint before it answers. */
function meAnswersLater() {
  server.use(
    http.get('/api/me', async () => {
      await delay('infinite')
      return HttpResponse.json(CURRENT_USER)
    }),
  )
}

describe('useTheme', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    document.documentElement.classList.remove('dark')
  })

  it('throws when used outside a ThemeProvider', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => renderHook(() => useTheme())).toThrow(
      /must be used within a ThemeProvider/,
    )
  })

  it('follows the system for an account that never chose', async () => {
    const { result } = renderTheme()

    await waitFor(() => expect(result.current.theme).toBe('system'))
  })

  it('paints the cached theme before the account answers', () => {
    localStorage.setItem(THEME_CACHE_KEY, 'dark')
    meAnswersLater()

    const { result } = renderTheme()

    expect(result.current.theme).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('ignores a cached value it does not know', () => {
    localStorage.setItem(THEME_CACHE_KEY, 'neon')
    meAnswersLater()

    const { result } = renderTheme()

    expect(result.current.theme).toBe('system')
  })

  it("follows the account over this browser's cache", async () => {
    localStorage.setItem(THEME_CACHE_KEY, 'light')
    server.use(
      http.get('/api/me', () => HttpResponse.json({ ...CURRENT_USER, theme: 'dark' })),
    )

    const { result } = renderTheme()

    await waitFor(() => expect(result.current.theme).toBe('dark'))
    expect(localStorage.getItem(THEME_CACHE_KEY)).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('saves a choice on the account, caches it and applies it', async () => {
    const { result } = renderTheme()
    await waitFor(() => expect(result.current.theme).toBe('system'))

    act(() => result.current.setTheme('dark'))

    expect(result.current.theme).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    await waitFor(() => expect(currentUser().theme).toBe('dark'))
    expect(localStorage.getItem(THEME_CACHE_KEY)).toBe('dark')
  })

  it('gives every consumer under one provider the same theme', async () => {
    const { result } = renderTheme(() => ({ first: useTheme(), second: useTheme() }))
    await waitFor(() => expect(result.current.first.theme).toBe('system'))

    act(() => result.current.first.setTheme('light'))

    expect(result.current.second.theme).toBe('light')
  })

  it('goes back to the saved theme when saving fails', async () => {
    server.use(
      http.get('/api/me', () => HttpResponse.json({ ...CURRENT_USER, theme: 'light' })),
      http.patch('/api/me', () =>
        HttpResponse.json({ error: 'internal_error' }, { status: 500 }),
      ),
    )
    const { result } = renderTheme()
    await waitFor(() => expect(result.current.theme).toBe('light'))

    act(() => result.current.setTheme('dark'))
    expect(result.current.theme).toBe('dark')

    await waitFor(() => expect(result.current.theme).toBe('light'))
  })
})
