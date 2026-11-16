import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse, delay } from 'msw'
import { server } from '@tests/mock/server'
import { createWrapper } from '@tests/utils'
import { NavModeProvider, useNavMode } from '@/hooks/useNavMode'
import { CURRENT_USER, currentUser } from '@/mock/fixtures/users'
import { NAV_MODE_CACHE_KEY } from '@/queries/auth'

function renderNavMode() {
  const { queryClient, wrapper: Base } = createWrapper()
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <Base>
      <NavModeProvider>{children}</NavModeProvider>
    </Base>
  )
  return { queryClient, ...renderHook(() => useNavMode(), { wrapper }) }
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

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useNavMode', () => {
  it('needs its provider', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() => renderHook(() => useNavMode())).toThrow(/NavModeProvider/)
  })

  it('starts expanded for a user who never chose', async () => {
    const { result } = renderNavMode()

    await waitFor(() => expect(result.current.mode).toBe('expanded'))
  })

  it('paints the cached mode before the account answers', () => {
    localStorage.setItem(NAV_MODE_CACHE_KEY, 'collapsed')
    meAnswersLater()

    const { result } = renderNavMode()

    expect(result.current.mode).toBe('collapsed')
  })

  it('ignores a cached value it does not know', () => {
    localStorage.setItem(NAV_MODE_CACHE_KEY, 'floating')
    meAnswersLater()

    const { result } = renderNavMode()

    expect(result.current.mode).toBe('expanded')
  })

  it("follows the account over this browser's cache", async () => {
    localStorage.setItem(NAV_MODE_CACHE_KEY, 'collapsed')
    server.use(
      http.get('/api/me', () => HttpResponse.json({ ...CURRENT_USER, sidebar: 'hover' })),
    )

    const { result } = renderNavMode()

    await waitFor(() => expect(result.current.mode).toBe('hover'))
    expect(localStorage.getItem(NAV_MODE_CACHE_KEY)).toBe('hover')
  })

  it('treats an account that never chose as the default, whatever was cached', async () => {
    localStorage.setItem(NAV_MODE_CACHE_KEY, 'collapsed')

    const { result } = renderNavMode()

    await waitFor(() => expect(result.current.mode).toBe('expanded'))
  })

  it('saves a choice on the account and caches it', async () => {
    const { result } = renderNavMode()
    await waitFor(() => expect(result.current.mode).toBe('expanded'))

    act(() => result.current.setMode('collapsed'))

    expect(result.current.mode).toBe('collapsed')
    await waitFor(() => expect(currentUser().sidebar).toBe('collapsed'))
    expect(localStorage.getItem(NAV_MODE_CACHE_KEY)).toBe('collapsed')
  })

  it('toggles between expanded and collapsed, and pins expand-on-hover open', async () => {
    server.use(
      http.get('/api/me', () => HttpResponse.json({ ...CURRENT_USER, sidebar: 'hover' })),
    )
    const { result } = renderNavMode()
    await waitFor(() => expect(result.current.mode).toBe('hover'))

    act(() => result.current.toggle())
    expect(result.current.mode).toBe('expanded')

    act(() => result.current.toggle())
    expect(result.current.mode).toBe('collapsed')
  })

  it('goes back to the saved choice when saving fails', async () => {
    let saved: string | null = 'collapsed'
    server.use(
      http.get('/api/me', () => HttpResponse.json({ ...CURRENT_USER, sidebar: saved })),
      http.patch('/api/me', () => {
        saved = 'collapsed'
        return HttpResponse.json({ error: 'internal_error' }, { status: 500 })
      }),
    )
    const { result } = renderNavMode()
    await waitFor(() => expect(result.current.mode).toBe('collapsed'))

    act(() => result.current.setMode('expanded'))
    expect(result.current.mode).toBe('expanded')

    await waitFor(() => expect(result.current.mode).toBe('collapsed'))
  })

  it('keeps working when this browser cannot store anything', async () => {
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })

    const { result } = renderNavMode()
    await waitFor(() => expect(result.current.mode).toBe('expanded'))
    act(() => result.current.setMode('collapsed'))

    expect(result.current.mode).toBe('collapsed')
    await waitFor(() => expect(currentUser().sidebar).toBe('collapsed'))
  })
})
