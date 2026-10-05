import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// setup.ts stubs the loader for every other test; this file tests the real one.
vi.unmock('@/features/worksheet/monacoLoader')

const { config, bundle } = vi.hoisted(() => ({
  config: vi.fn(),
  bundle: { fail: false },
}))

vi.mock('@monaco-editor/react', () => ({ loader: { config } }))
vi.mock('@/features/worksheet/monacoBundle', () => ({
  get monaco() {
    if (bundle.fail) throw new Error('chunk failed to load')
    return { bundled: true }
  },
}))

// The loader keeps module-level state, so each test gets fresh modules.
async function importLoader() {
  return import('@/features/worksheet/monacoLoader')
}

beforeEach(() => {
  vi.resetModules()
  config.mockClear()
  bundle.fail = false
})

describe('loadMonaco', () => {
  it('hands the bundled Monaco to @monaco-editor/react instead of the CDN', async () => {
    const { loadMonaco } = await importLoader()

    await loadMonaco()

    expect(config).toHaveBeenCalledExactlyOnceWith({ monaco: { bundled: true } })
  })

  it('configures the loader only once across editors', async () => {
    const { loadMonaco } = await importLoader()

    await Promise.all([loadMonaco(), loadMonaco()])
    await loadMonaco()

    expect(config).toHaveBeenCalledOnce()
  })
})

describe('useMonacoReady', () => {
  it('turns true once Monaco is configured', async () => {
    const { useMonacoReady } = await importLoader()

    const { result } = renderHook(() => useMonacoReady())

    expect(result.current).toBe(false)
    await waitFor(() => expect(result.current).toBe(true))
    expect(config).toHaveBeenCalledOnce()
  })

  it('is true from the first render once Monaco has loaded', async () => {
    const { loadMonaco, useMonacoReady } = await importLoader()
    await loadMonaco()

    const { result } = renderHook(() => useMonacoReady())

    expect(result.current).toBe(true)
  })

  it('stays false and logs when the Monaco chunk fails to load', async () => {
    bundle.fail = true
    const error = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const { useMonacoReady } = await importLoader()

    const { result } = renderHook(() => useMonacoReady())

    await waitFor(() =>
      expect(error).toHaveBeenCalledWith('Failed to load the SQL editor', expect.any(Error)),
    )
    expect(result.current).toBe(false)
    expect(config).not.toHaveBeenCalled()
    error.mockRestore()
  })
})
