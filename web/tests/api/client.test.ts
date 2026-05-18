import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ApiError, get, post, del } from '@/api/client'

function mockFetch(status: number, body?: unknown) {
  const response = {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response

  vi.spyOn(global, 'fetch').mockResolvedValue(response)
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('ApiError', () => {
  it('carries status and message', () => {
    const err = new ApiError(404, 'not found')
    expect(err.status).toBe(404)
    expect(err.message).toBe('not found')
    expect(err.name).toBe('ApiError')
    expect(err instanceof Error).toBe(true)
  })
})

describe('get()', () => {
  it('resolves with parsed JSON on 200', async () => {
    mockFetch(200, { hello: 'world' })
    const result = await get<{ hello: string }>('/test')
    expect(result).toEqual({ hello: 'world' })
  })

  it('resolves to undefined on 204', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 204,
      json: () => Promise.reject(new Error('no body')),
    } as unknown as Response)
    const result = await get('/test')
    expect(result).toBeUndefined()
  })

  it('throws ApiError with body.error message on 4xx', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 401,
      json: () => Promise.resolve({ error: 'Unauthorized' }),
    } as unknown as Response)
    await expect(get('/test')).rejects.toMatchObject({
      name: 'ApiError',
      status: 401,
      message: 'Unauthorized',
    })
  })

  it('falls back to "HTTP 500" when body is not JSON', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.reject(new SyntaxError('unexpected token')),
    } as unknown as Response)
    await expect(get('/test')).rejects.toMatchObject({
      name: 'ApiError',
      status: 500,
      message: 'HTTP 500',
    })
  })
})

describe('post()', () => {
  it('sends JSON body with correct Content-Type', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ id: 1 }),
    } as unknown as Response)

    await post('/items', { name: 'test' })

    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/items',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ name: 'test' }),
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      }),
    )
  })
})

describe('del()', () => {
  it('uses DELETE method', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      status: 204,
      json: () => Promise.reject(new Error('no body')),
    } as unknown as Response)

    await del('/items/1')

    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/items/1',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })
})
