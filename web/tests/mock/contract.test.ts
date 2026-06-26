import { describe, it, expect } from 'vitest'
import { server } from '@tests/mock/server'
import { http } from 'msw'
import { ApiError } from '@/api/client'
import { workspacesApi } from '@/api/workspaces'
import { queriesApi } from '@/api/queries'
import { storageBackendsApi } from '@/api/storage-backends'
import { agentsApi } from '@/api/agents'

// Each mocked endpoint must mirror the authoritative backend *Out schema. These
// assert the realigned shapes; error paths use the built-in triggers + overrides.

describe('workspaces contract', () => {
  it('members are MemberOut-shaped (workspace_id, user_id, role; no email/name)', async () => {
    const members = await workspacesApi.members('acme-analytics')
    expect(members.length).toBeGreaterThan(0)
    for (const m of members) {
      expect(Object.keys(m).sort()).toEqual(['role', 'user_id', 'workspace_id'])
      expect(m.workspace_id).toBe('ws-1')
    }
  })

  it('POST /workspaces creates a name-only workspace with no catalog/storage', async () => {
    const ws = await workspacesApi.create({ slug: 'new-ws', name: 'new-ws' })
    expect(ws.default_catalog).toBeNull()
    expect(ws.storage_backend_kind).toBeNull()
    expect(ws.id).toBe('ws-new-1') // deterministic id
  })

  it('POST .../members adds a member statefully', async () => {
    const created = await workspacesApi.members('home-lab')
    const before = created.length
    await fetch('/api/workspaces/home-lab/members', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: 'u-2', role: 'writer' }),
    })
    const after = await workspacesApi.members('home-lab')
    expect(after).toHaveLength(before + 1)
    expect(after.at(-1)).toMatchObject({ user_id: 'u-2', role: 'writer' })
  })

  it('404 on unknown workspace with a {detail} envelope', async () => {
    await expect(workspacesApi.members('nope')).rejects.toMatchObject({
      name: 'ApiError',
      status: 404,
    })
  })
})

describe('queries contract', () => {
  it('dispatch returns a full QueryOut (202)', async () => {
    const q = await queriesApi.dispatch('acme-analytics', 'SELECT 1', 'ag-1')
    expect(q).toMatchObject({
      workspace_id: 'ws-1',
      user_id: 'u-1',
      agent_id: 'ag-1',
      sql: 'SELECT 1',
      status: 'queued',
    })
    expect(q.id).toBe('q-new-1')
    expect(typeof q.started_at).toBe('string')
  })

  it('dispatch rejects sandbox-escaping SQL with a 422', async () => {
    await expect(
      queriesApi.dispatch('acme-analytics', "ATTACH 'evil.db' AS evil", 'ag-1'),
    ).rejects.toMatchObject({ name: 'ApiError', status: 422 })
  })

  it('rows returns 409 while a query is not done', async () => {
    const q = await queriesApi.dispatch('acme-analytics', 'SELECT 1', 'ag-1')
    await expect(queriesApi.rows(q.id)).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
    })
  })

  it('saved-query created_by is a user id, not an email', async () => {
    const saved = await queriesApi.save('acme-analytics', {
      name: 'q',
      sql: 'SELECT 1',
    })
    expect(saved.created_by).toBe('u-1')
    expect(saved.id).toBe('sq-new-1')
  })

  it('cross-workspace log filters by user_id and orders started_at DESC', async () => {
    const rows = await queriesApi.listForWorkspace('acme-analytics', {
      all_workspaces: true,
      user_id: 'u-1',
    })
    expect(rows.every((r) => r.user_id === 'u-1')).toBe(true)
    const times = rows.map((r) => r.started_at)
    expect(times).toEqual([...times].sort((a, b) => b.localeCompare(a)))
  })
})

describe('storage backends contract', () => {
  it('created_by is a user id', async () => {
    const list = await storageBackendsApi.list()
    expect(list.every((b) => b.created_by === 'u-1')).toBe(true)
  })

  it('409 when deleting a backend still in use', async () => {
    await expect(storageBackendsApi.remove('sb-1')).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
    })
  })
})

describe('agents contract', () => {
  it('bootstrap token is deterministic', async () => {
    const a = await agentsApi.bootstrap()
    expect(a.token).toBe('dh_boot_seed000000000001')
  })

  it('surfaces a 401 from a handler override', async () => {
    server.use(http.get('/api/agents', () => new Response('x', { status: 401 })))
    await expect(agentsApi.list()).rejects.toBeInstanceOf(ApiError)
  })
})
