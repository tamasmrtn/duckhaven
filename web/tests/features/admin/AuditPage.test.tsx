import { describe, it, expect } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@tests/utils'
import { QUERY_HISTORY } from '@/mock/fixtures/queries'

const AUDIT_ROUTE = '/acme-analytics/admin/audit'

describe('AuditPage', () => {
  it('renders the audit table', async () => {
    renderWithProviders({ initialRoute: AUDIT_ROUTE })
    await screen.findByText(`${QUERY_HISTORY.length} entries`)
  })

  it('filtering by an unknown user_id narrows the result to zero', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: AUDIT_ROUTE })
    await screen.findByText(`${QUERY_HISTORY.length} entries`)

    const input = await screen.findByLabelText(/filter by user id/i)
    await user.type(input, 'no-such-user')

    await waitFor(() => {
      expect(screen.getByText('0 entries')).toBeInTheDocument()
    })
  })
})
