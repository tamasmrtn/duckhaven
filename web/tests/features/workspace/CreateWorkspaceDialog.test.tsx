import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createTestQueryClient } from '@tests/utils'
import { CreateWorkspaceDialog } from '@/features/workspace/CreateWorkspaceDialog'

describe('CreateWorkspaceDialog', () => {
  it('has an accessible description so Radix does not warn (BUG-9)', async () => {
    const queryClient = createTestQueryClient()
    render(
      <QueryClientProvider client={queryClient}>
        <CreateWorkspaceDialog
          open
          onOpenChange={() => {}}
          onCreated={() => {}}
        />
      </QueryClientProvider>,
    )

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAccessibleDescription(/storage backend/i)
  })
})
