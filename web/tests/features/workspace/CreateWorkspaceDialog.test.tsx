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
    expect(dialog).toHaveAccessibleDescription(/storage/i)
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
    // A workspace is created name-only and starts with no catalog; storage is
    // chosen later, per catalog. The request carries no storage_backend_id.
    expect(body).toBeDefined()
    expect(body).not.toHaveProperty('storage_backend_id')
    expect(body).toMatchObject({ slug: 'analytics', name: 'Analytics' })
  })
})
