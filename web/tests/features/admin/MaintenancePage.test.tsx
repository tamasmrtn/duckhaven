import { describe, it, expect } from 'vitest'
import { screen, fireEvent, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'

const MAINTENANCE_ROUTE = '/acme-analytics/admin/maintenance'

describe('MaintenancePage', () => {
  it('renders the scanning, profile and manual-scan sections', async () => {
    renderWithProviders({ initialRoute: MAINTENANCE_ROUTE })

    expect(await screen.findByText('Autonomous scanning')).toBeInTheDocument()
    expect(screen.getByText('Maintenance profile')).toBeInTheDocument()
    expect(screen.getByText('Run a scan now')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /scan now/i })).toBeInTheDocument()
  })

  // Regression: scanning on/off was a dark "Enabled" button, which read as an
  // action to take rather than the state of a setting.
  it('shows autonomous scanning as a switch and turns it off', async () => {
    const sent: unknown[] = []
    server.use(
      http.put('/api/admin/maintenance/policy', async ({ request }) => {
        const body = await request.json()
        sent.push(body)
        return HttpResponse.json(body)
      }),
    )
    renderWithProviders({ initialRoute: MAINTENANCE_ROUTE })

    const toggle = await screen.findByRole('switch', { name: 'Autonomous scanning' })
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    expect(screen.queryByRole('button', { name: 'Enabled' })).not.toBeInTheDocument()

    fireEvent.click(toggle)
    await waitFor(() => expect(sent).toContainEqual({ scan_enabled: false }))
  })

  it('marks the active preset from the policy as pressed', async () => {
    renderWithProviders({ initialRoute: MAINTENANCE_ROUTE })

    const balanced = await screen.findByRole('button', { name: /Balanced/ })
    expect(balanced).toHaveAttribute('aria-pressed', 'true')
    expect(
      screen.getByRole('button', { name: /Conservative/ }),
    ).toHaveAttribute('aria-pressed', 'false')
  })

  it('reveals the advanced threshold overrides on demand', async () => {
    renderWithProviders({ initialRoute: MAINTENANCE_ROUTE })

    const toggle = await screen.findByText(/show advanced thresholds/i)
    expect(screen.queryByText('small_file_ratio_warn')).not.toBeInTheDocument()
    fireEvent.click(toggle)
    expect(screen.getByText('small_file_ratio_warn')).toBeInTheDocument()
  })
})
