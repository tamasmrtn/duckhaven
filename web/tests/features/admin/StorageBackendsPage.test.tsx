import { describe, it, expect } from 'vitest'
import { screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'

const STORAGE_ROUTE = '/acme-analytics/admin/storage'

describe('StorageBackendsPage DR banner', () => {
  it('warns about off-box DR when an object_store backend exists', async () => {
    // The MSW fixtures seed bundled object_store backends.
    renderWithProviders({ initialRoute: STORAGE_ROUTE })
    expect(
      await screen.findByText(/no off-box disaster recovery/i),
    ).toBeInTheDocument()
  })

  it('hides the DR banner when only external backends exist', async () => {
    server.use(
      http.get('/api/admin/storage-backends', () =>
        HttpResponse.json([
          {
            id: 'sb-1',
            name: 'acme-prod',
            kind: 's3',
            root_uri: 's3://bucket/',
            config: { role_arn: 'arn:aws:iam::1:role/x', region: 'us-east-1' },
            workspace_count: 1,
            created_by: 'u-1',
            created_at: new Date().toISOString(),
          },
        ]),
      ),
    )
    renderWithProviders({ initialRoute: STORAGE_ROUTE })
    expect(await screen.findByText('1 backend')).toBeInTheDocument()
    expect(
      screen.queryByText(/no off-box disaster recovery/i),
    ).not.toBeInTheDocument()
  })
})

describe('StorageBackendsPage health check', () => {
  it('runs a health check and shows the result for an external backend', async () => {
    server.use(
      http.get('/api/admin/storage-backends', () =>
        HttpResponse.json([
          {
            id: 'sb-1',
            name: 'acme-prod',
            kind: 's3',
            root_uri: 's3://bucket/',
            config: { role_arn: 'arn:aws:iam::1:role/x', region: 'us-east-1' },
            workspace_count: 0,
            created_by: 'u-1',
            created_at: new Date().toISOString(),
          },
        ]),
      ),
      http.post('/api/admin/storage-backends/sb-1/health', () =>
        HttpResponse.json({ valid: true, detail: 'Vended credentials reached storage.' }),
      ),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: STORAGE_ROUTE })

    const row = (await screen.findByText('acme-prod')).closest('tr')!
    await user.click(within(row).getByRole('button', { name: /test access/i }))

    // A valid result renders the success shield icon in the row.
    await waitFor(() =>
      expect(row.querySelector('.lucide-shield-check')).toBeInTheDocument(),
    )
  })

  it('offers the health check on the bundled backend too', async () => {
    // The bundled store used to render a dash here, so a store that was down
    // or a bucket that was never created was invisible to the operator.
    server.use(
      http.get('/api/admin/storage-backends', () =>
        HttpResponse.json([
          {
            id: 'sb-bundled',
            name: 'bundled',
            kind: 'object_store',
            root_uri: '',
            config: null,
            workspace_count: 0,
            created_by: 'u-1',
            created_at: new Date().toISOString(),
          },
        ]),
      ),
      http.post('/api/admin/storage-backends/sb-bundled/health', () =>
        HttpResponse.json({
          valid: false,
          detail: 'Could not reach the bundled bucket: connection refused',
        }),
      ),
    )
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: STORAGE_ROUTE })

    const row = (await screen.findByText('bundled')).closest('tr')!
    await user.click(within(row).getByRole('button', { name: /test access/i }))

    // An invalid result renders the failure shield icon in the row.
    await waitFor(() =>
      expect(row.querySelector('.lucide-shield-x')).toBeInTheDocument(),
    )
  })
})
