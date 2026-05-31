import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'

const STORAGE_ROUTE = '/acme-analytics/admin/storage'

describe('StorageBackendsPage DR banner', () => {
  it('warns about off-box DR when a local/NAS backend exists', async () => {
    // The MSW fixtures seed both a NAS and a local_fs backend.
    renderWithProviders({ initialRoute: STORAGE_ROUTE })
    expect(
      await screen.findByText(/no off-box disaster recovery/i),
    ).toBeInTheDocument()
  })

  it('uses the singular noun for a single backend (Bug #8)', async () => {
    server.use(
      http.get('/api/admin/storage-backends', () =>
        HttpResponse.json([
          {
            id: 'sb-1',
            name: 'only-one',
            kind: 's3',
            location: 's3://bucket/',
            workspace_count: 1,
            created_at: new Date().toISOString(),
          },
        ]),
      ),
    )
    renderWithProviders({ initialRoute: STORAGE_ROUTE })
    expect(await screen.findByText('1 backend')).toBeInTheDocument()
    expect(screen.queryByText('1 backends')).not.toBeInTheDocument()
  })
})
