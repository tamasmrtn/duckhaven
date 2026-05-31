import { describe, it, expect, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useIsDark } from '@/hooks/useIsDark'

describe('useIsDark', () => {
  afterEach(() => {
    document.documentElement.classList.remove('dark')
  })

  it('reflects the initial dark class on <html>', () => {
    document.documentElement.classList.add('dark')
    const { result } = renderHook(() => useIsDark())
    expect(result.current).toBe(true)
  })

  it('reacts when the dark class is toggled', async () => {
    document.documentElement.classList.remove('dark')
    const { result } = renderHook(() => useIsDark())
    expect(result.current).toBe(false)

    await act(async () => {
      document.documentElement.classList.add('dark')
    })
    expect(result.current).toBe(true)

    await act(async () => {
      document.documentElement.classList.remove('dark')
    })
    expect(result.current).toBe(false)
  })
})
