import { describe, it, expect } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import {
  useAttachCatalog,
  useCatalogs,
  useCreateCatalog,
  useDetachCatalog,
} from '@/queries/catalogs'
import { createWrapper } from '@tests/utils'

describe('catalog hooks', () => {
  it('lists the catalogs attached to a workspace', async () => {
    const { wrapper } = createWrapper()
    const { result } = renderHook(() => useCatalogs('acme-analytics'), {
      wrapper,
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    const slugs = result.current.data!.map((c) => c.slug).sort()
    expect(slugs).toEqual(['acme_analytics', 'curated'])
    expect(
      result.current.data!.find((c) => c.slug === 'acme_analytics')!.is_default,
    ).toBe(true)
  })

  it('creates a catalog and it appears in the workspace listing', async () => {
    const { wrapper } = createWrapper()
    const create = renderHook(() => useCreateCatalog('home-lab'), { wrapper })
    await act(async () => {
      await create.result.current.mutateAsync({ name: 'staging' })
    })
    const list = renderHook(() => useCatalogs('home-lab'), { wrapper })
    await waitFor(() => expect(list.result.current.isSuccess).toBe(true))
    expect(list.result.current.data!.some((c) => c.slug === 'staging')).toBe(true)
  })

  it('attaches an existing catalog to a second workspace (M:N)', async () => {
    const { wrapper } = createWrapper()
    const attach = renderHook(() => useAttachCatalog('acme-research'), { wrapper })
    let attached
    await act(async () => {
      attached = await attach.result.current.mutateAsync({ catalogId: 'cat-curated' })
    })
    // The same catalog now reports two bindings.
    expect((attached as { attached_workspaces: number }).attached_workspaces).toBe(2)
  })

  it('detaches a catalog from a workspace', async () => {
    const { wrapper } = createWrapper()
    const detach = renderHook(() => useDetachCatalog('acme-analytics'), { wrapper })
    await act(async () => {
      await detach.result.current.mutateAsync('curated')
    })
    const list = renderHook(() => useCatalogs('acme-analytics'), { wrapper })
    await waitFor(() => expect(list.result.current.isSuccess).toBe(true))
    expect(list.result.current.data!.some((c) => c.slug === 'curated')).toBe(false)
  })
})
