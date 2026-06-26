import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClientProvider } from '@tanstack/react-query'
import { server } from '@tests/mock/server'
import { createTestQueryClient } from '@tests/utils'
import { CreateCatalogDialog } from '@/features/catalog/CatalogDialogs'

function renderDialog() {
  const queryClient = createTestQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <CreateCatalogDialog ws="acme-analytics" open onOpenChange={() => {}} />
    </QueryClientProvider>,
  )
}

describe('CreateCatalogDialog', () => {
  it('creates a catalog on bundled storage by default (no backend chosen)', async () => {
    const user = userEvent.setup()
    let body: Record<string, unknown> | undefined
    server.use(
      http.post('/api/workspaces/:ws/catalogs', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          { id: 'cat-new', slug: body.slug, name: body.name },
          { status: 201 },
        )
      }),
    )
    renderDialog()

    await user.type(await screen.findByLabelText('Name'), 'staging')
    await user.click(screen.getByRole('button', { name: /^create$/i }))

    await vi.waitFor(() => expect(body).toBeDefined())
    // Storage defaults to bundled object storage → no backend id sent.
    expect(body!.storage_backend_id).toBeUndefined()
    expect(body).toMatchObject({ name: 'staging' })
    expect(body).not.toHaveProperty('slug')
  })

  it('advanced flow creates an external backend then the catalog on it', async () => {
    const user = userEvent.setup()
    let backendBody: Record<string, unknown> | undefined
    let catalogBody: Record<string, unknown> | undefined
    server.use(
      http.post('/api/admin/storage-backends', async ({ request }) => {
        backendBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          {
            id: 'sb-ext',
            kind: backendBody.kind,
            name: backendBody.name,
            root_uri: backendBody.root_uri,
            uc_storage_credential_id: null,
            uc_credential_valid: null,
            workspace_count: 0,
            created_by: 'u-1',
            created_at: new Date().toISOString(),
          },
          { status: 201 },
        )
      }),
      http.post('/api/workspaces/:ws/catalogs', async ({ request }) => {
        catalogBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          { id: 'cat-ext', slug: catalogBody.slug, name: catalogBody.name },
          { status: 201 },
        )
      }),
    )
    renderDialog()

    await user.type(await screen.findByLabelText('Name'), 'archive')
    // The storage picker is shown up front (not behind Advanced); pick a new
    // external backend and fill its details.
    await user.selectOptions(
      await screen.findByLabelText('Storage backend'),
      '__new',
    )
    await user.type(screen.getByLabelText('Backend name'), 'acme-prod')
    await user.type(screen.getByLabelText('Root URI'), 's3://acme/duckhaven/')
    await user.click(screen.getByRole('button', { name: /^create$/i }))

    await vi.waitFor(() => expect(catalogBody).toBeDefined())
    expect(backendBody).toMatchObject({
      kind: 's3',
      name: 'acme-prod',
      root_uri: 's3://acme/duckhaven/',
    })
    expect(catalogBody).toMatchObject({
      name: 'archive',
      storage_backend_id: 'sb-ext',
    })
  })
})
