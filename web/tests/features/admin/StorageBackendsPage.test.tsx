import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
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
})
