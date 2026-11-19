import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'
import { CURRENT_USER } from '@/mock/fixtures/users'

async function openSwitcher() {
  const user = userEvent.setup()
  renderWithProviders({ initialRoute: '/acme-analytics/worksheets' })
  await user.click(await screen.findByRole('button', { name: /switch workspace/i }))
  return screen.findByRole('dialog', { name: /switch workspace/i })
}

describe('WorkspaceSwitcher', () => {
  it('offers to create a workspace with workspaces:create', async () => {
    await openSwitcher()
    expect(await screen.findByRole('option', { name: /create workspace/i })).toBeInTheDocument()
  })

  it('does not offer it without workspaces:create', async () => {
    server.use(
      http.get('/api/me', () =>
        HttpResponse.json({ ...CURRENT_USER, role: 'user', permissions: [] }),
      ),
    )
    await openSwitcher()
    expect(await screen.findByRole('option', { name: /acme-analytics/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /create workspace/i })).not.toBeInTheDocument()
  })
})
