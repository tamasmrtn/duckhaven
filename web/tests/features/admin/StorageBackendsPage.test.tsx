import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
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
            uc_storage_credential_id: null,
            uc_credential_valid: null,
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
