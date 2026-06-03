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

  it('resolves workspace and agent ids to slug/name', async () => {
    renderWithProviders({ initialRoute: AUDIT_ROUTE })
    await screen.findByText(`${QUERY_HISTORY.length} entries`)
    // ws-2 → acme-research slug; ag-2 → agent-b name.
    expect(await screen.findByText('acme-research')).toBeInTheDocument()
    expect(screen.getAllByText('agent-b').length).toBeGreaterThan(0)
    // The raw UUID-shaped ids must not be shown when a mapping exists.
    expect(screen.queryByText('ws-2')).not.toBeInTheDocument()
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
