import { describe, it, expect } from 'vitest'
import { workspacesApi } from '@/api/workspaces'
import { resetMockState } from '@/mock/reset'
import { WORKSPACES } from '@/mock/fixtures/workspaces'

// resetMockState() (run in afterEach) must restore mutated stores so tests stay
// isolated — module-level fixture arrays otherwise leak across tests.
describe('resetMockState()', () => {
  it('restores a store mutated by a POST', async () => {
    const before = WORKSPACES.length
    await workspacesApi.create({
      slug: 'temp',
      name: 'temp',
      storage_backend_id: 'sb-4',
    })
    expect(WORKSPACES.length).toBe(before + 1)

    resetMockState()
    expect(WORKSPACES.length).toBe(before)
  })

  it('seed counter is reset, so created ids are stable across tests', async () => {
    const ws = await workspacesApi.create({
      slug: 'temp2',
      name: 'temp2',
      storage_backend_id: 'sb-4',
    })
    // afterEach reset means the counter starts at 1 every test
    expect(ws.id).toBe('ws-new-1')
  })
})
