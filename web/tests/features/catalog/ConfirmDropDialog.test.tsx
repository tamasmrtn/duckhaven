import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmDropDialog } from '@/features/catalog/ConfirmDropDialog'
import { ApiError } from '@/api/client'

describe('ConfirmDropDialog cascade gating', () => {
  it('surfaces the checkbox on 409 and gates confirm until it is checked', async () => {
    const user = userEvent.setup()
    // First confirm (no cascade) is rejected 409; the second succeeds.
    const onConfirm = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, 'Schema is not empty'))
      .mockResolvedValueOnce(undefined)

    render(
      <ConfirmDropDialog
        open
        onOpenChange={() => {}}
        kind="schema"
        name="sales"
        onConfirm={onConfirm}
        pending={false}
      />,
    )

    // Type the name to arm the destructive confirm, then attempt the drop.
    await user.type(screen.getByLabelText(/type .* to confirm/i), 'sales')
    const dropBtn = screen.getByRole('button', { name: /drop schema/i })
    await user.click(dropBtn)

    // The 409 reveals the cascade checkbox and re-disables confirm.
    const checkbox = await screen.findByRole('checkbox', {
      name: /also drop all tables/i,
    })
    expect(onConfirm).toHaveBeenNthCalledWith(1, false)
    await waitFor(() => expect(dropBtn).toBeDisabled())

    // Checking the box re-enables confirm; the second attempt passes cascade.
    await user.click(checkbox)
    await waitFor(() => expect(dropBtn).toBeEnabled())
    await user.click(dropBtn)
    expect(onConfirm).toHaveBeenNthCalledWith(2, true)
  })
})
