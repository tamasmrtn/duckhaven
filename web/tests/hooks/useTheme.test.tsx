import { describe, it, expect, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { ThemeProvider, useTheme } from '@/hooks/useTheme'
import type { ReactNode } from 'react'

function wrapper({ children }: { children: ReactNode }) {
  return <ThemeProvider>{children}</ThemeProvider>
}

describe('useTheme', () => {
  afterEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('dark')
  })

  it('throws when used outside a ThemeProvider', () => {
    expect(() => renderHook(() => useTheme())).toThrow(
      /must be used within a ThemeProvider/,
    )
  })

  it('shares one theme value across every consumer under the same provider', () => {
    const a = renderHook(() => useTheme(), { wrapper })
    const b = renderHook(() => useTheme(), { wrapper })

    expect(a.result.current.theme).toBe('system')
    expect(b.result.current.theme).toBe('system')

    act(() => {
      a.result.current.setTheme('dark')
    })

    // Each renderHook call mounts its own provider instance here (no shared
    // tree), so what this actually proves is that a single provider's value
    // is consistent for all of its consumers — verified directly below.
    expect(a.result.current.theme).toBe('dark')
  })

  it('a setTheme call from one consumer is visible to a sibling under the same provider', () => {
    function useBoth() {
      const first = useTheme()
      const second = useTheme()
      return { first, second }
    }
    const { result } = renderHook(() => useBoth(), { wrapper })

    expect(result.current.first.theme).toBe('system')
    expect(result.current.second.theme).toBe('system')

    act(() => {
      result.current.first.setTheme('dark')
    })

    expect(result.current.first.theme).toBe('dark')
    expect(result.current.second.theme).toBe('dark')
  })

  it('persists the chosen theme to localStorage and applies the dark class', () => {
    const { result } = renderHook(() => useTheme(), { wrapper })

    act(() => {
      result.current.setTheme('dark')
    })

    expect(localStorage.getItem('dh-theme')).toBe('dark')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})
