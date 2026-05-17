import '@testing-library/jest-dom/vitest'
import { beforeAll, afterEach, afterAll, vi } from 'vitest'
import { server } from './mock/server'

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// Monaco doesn't work in jsdom; stub it out globally
vi.mock('@monaco-editor/react', () => ({
  Editor: () => null,
  default: () => null,
}))

// localStorage may be missing or incomplete in some jsdom workers
const store: Record<string, string> = {}
const localStorageMock: Storage = {
  getItem: (key) => store[key] ?? null,
  setItem: (key, value) => { store[key] = value },
  removeItem: (key) => { delete store[key] },
  clear: () => { Object.keys(store).forEach((k) => { delete store[k] }) },
  key: (index) => Object.keys(store)[index] ?? null,
  get length() { return Object.keys(store).length },
}
Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
  writable: true,
  configurable: true,
})

// clipboard API is not available in jsdom
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText: vi.fn().mockResolvedValue(undefined) },
  writable: true,
  configurable: true,
})

// matchMedia not implemented in jsdom
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  configurable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})
