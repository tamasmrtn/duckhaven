import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { QueryClientProvider } from '@tanstack/react-query'
import { server } from '@tests/mock/server'
import { createTestQueryClient } from '@tests/utils'
import { CreateWorkspaceDialog } from '@/features/workspace/CreateWorkspaceDialog'

function renderDialog(onCreated = vi.fn()) {
  const queryClient = createTestQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <CreateWorkspaceDialog open onOpenChange={() => {}} onCreated={onCreated} />
    </QueryClientProvider>,
  )
  return { onCreated }
}

describe('CreateWorkspaceDialog', () => {
  it('has an accessible description so Radix does not warn', async () => {
    renderDialog()
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAccessibleDescription(/object storage/i)
  })

  it('creates a name-only workspace without choosing a backend', async () => {
    const user = userEvent.setup()
    let body: Record<string, unknown> | undefined
    server.use(
      http.post('/api/workspaces', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          {
            id: 'ws-new',
            slug: body.slug,
            name: body.name,
            storage_backend_id: 'sb-auto',
            storage_backend_kind: 'object_store',
            created_at: new Date().toISOString(),
          },
          { status: 201 },
        )
      }),
    )
    const { onCreated } = renderDialog()

    await user.type(await screen.findByLabelText('Workspace name'), 'Analytics')
    await user.click(screen.getByRole('button', { name: /create workspace/i }))

    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledWith('analytics'))
    // The default flow omits storage_backend_id so the API auto-provisions
    // bundled object storage — the whole point of the simplification.
    expect(body).toBeDefined()
    expect(body).not.toHaveProperty('storage_backend_id')
    expect(body).toMatchObject({ slug: 'analytics', name: 'Analytics' })
  })

  it('advanced flow creates an external backend then the workspace', async () => {
    const user = userEvent.setup()
    let backendBody: Record<string, unknown> | undefined
    let wsBody: Record<string, unknown> | undefined
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
      http.post('/api/workspaces', async ({ request }) => {
        wsBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          {
            id: 'ws-ext',
            slug: wsBody.slug,
            name: wsBody.name,
            storage_backend_id: wsBody.storage_backend_id,
            storage_backend_kind: 's3',
            created_at: new Date().toISOString(),
          },
          { status: 201 },
        )
      }),
    )
    const { onCreated } = renderDialog()

    await user.type(await screen.findByLabelText('Workspace name'), 'Research')
    await user.click(screen.getByRole('button', { name: /advanced/i }))
    // Existing backends are listed, so opt into a new external one.
    await user.selectOptions(
      await screen.findByLabelText('Storage backend'),
      '__new',
    )
    // S3 is the default external kind; fill its details.
    await user.type(screen.getByLabelText('Backend name'), 'acme-prod')
    await user.type(
      screen.getByLabelText('Root URI'),
      's3://acme/duckhaven/',
    )
    await user.click(screen.getByRole('button', { name: /create workspace/i }))

    await vi.waitFor(() => expect(onCreated).toHaveBeenCalledWith('research'))
    expect(backendBody).toMatchObject({
      kind: 's3',
      name: 'acme-prod',
      root_uri: 's3://acme/duckhaven/',
    })
    expect(wsBody).toMatchObject({ storage_backend_id: 'sb-ext' })
  })
})
