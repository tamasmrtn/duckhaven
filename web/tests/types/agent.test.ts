import { describe, it, expect } from 'vitest'
import { agentSupportsBackend } from '@/types/agent'
import type { Agent } from '@/types/agent'

function makeAgent(extensions: string[]): Agent {
  return {
    id: 'ag-test',
    name: 'test-agent',
    status: 'healthy',
    capabilities: {
      duckdb_version: '1.5.2',
      extensions,
      memory_limit_gb: 6,
      cores: 4,
      tailscale_ip: null,
      host: null,
    },
    last_ping_at: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

describe('agentSupportsBackend()', () => {
  it('s3 requires httpfs extension', () => {
    expect(agentSupportsBackend(makeAgent(['httpfs', 'delta']), 's3')).toBe(true)
    expect(agentSupportsBackend(makeAgent(['delta']), 's3')).toBe(false)
  })

  it('adls_gen2 requires azure extension', () => {
    expect(agentSupportsBackend(makeAgent(['azure', 'httpfs']), 'adls_gen2')).toBe(true)
    expect(agentSupportsBackend(makeAgent(['httpfs']), 'adls_gen2')).toBe(false)
  })

  it('local_fs is always supported', () => {
    expect(agentSupportsBackend(makeAgent([]), 'local_fs')).toBe(true)
    expect(agentSupportsBackend(makeAgent(['httpfs', 'azure']), 'local_fs')).toBe(true)
  })

  it('nas is always supported', () => {
    expect(agentSupportsBackend(makeAgent([]), 'nas')).toBe(true)
    expect(agentSupportsBackend(makeAgent(['httpfs']), 'nas')).toBe(true)
  })
})
