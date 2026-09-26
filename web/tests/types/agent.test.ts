import { describe, it, expect } from 'vitest'
import {
  agentAvailability,
  agentRestartable,
  agentSupportsBackend,
  isBetaRuntime,
  runtimeLabel,
  runtimeRefusal,
} from '@/types/agent'
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
    expect(agentSupportsBackend(makeAgent(['httpfs', 'iceberg']), 's3')).toBe(true)
    expect(agentSupportsBackend(makeAgent(['iceberg']), 's3')).toBe(false)
  })

  it('adls_gen2 requires azure extension', () => {
    expect(agentSupportsBackend(makeAgent(['azure', 'httpfs']), 'adls_gen2')).toBe(true)
    expect(agentSupportsBackend(makeAgent(['httpfs']), 'adls_gen2')).toBe(false)
  })

  it('object_store requires httpfs (bundled S3 store)', () => {
    expect(agentSupportsBackend(makeAgent([]), 'object_store')).toBe(false)
    expect(agentSupportsBackend(makeAgent(['httpfs', 'azure']), 'object_store')).toBe(true)
  })
})

describe('runtimes', () => {
  const onRuntime = (runtime: Agent['runtime'], over: Partial<Agent> = {}): Agent => ({
    ...makeAgent(['httpfs']),
    runtime,
    ...over,
  })
  const ok = { id: '1.5', display_name: 'DuckDB 1.5', status: 'ga', state: 'ok' } as const

  it('labels an agent by runtime and exact engine', () => {
    const agent = onRuntime(ok)
    agent.capabilities!.engine_version = 'v1.5.5'
    expect(runtimeLabel(agent)).toBe('DuckDB 1.5 · v1.5.5')
  })

  it('falls back to the DuckDB version for an agent with no runtime', () => {
    expect(runtimeLabel(makeAgent([]))).toBe('DuckDB 1.5.2')
  })

  it.each([
    ['unrecognized', 'Not running a supported runtime'],
    ['mismatch', 'Running a different runtime than it was created with'],
    ['retired', 'Runtime DuckDB 1.5 is retired'],
  ] as const)('refuses work on a %s runtime', (state, reason) => {
    const agent = onRuntime({ ...ok, state })
    expect(runtimeRefusal(agent)).toBe(reason)
    expect(agentAvailability(agent, {})).toEqual({ kind: 'incompatible', reason })
  })

  it('treats ok and inferred runtimes as runnable', () => {
    expect(agentAvailability(onRuntime(ok), {})).toEqual({ kind: 'running' })
    expect(agentAvailability(onRuntime({ ...ok, state: 'inferred' }), {})).toEqual({
      kind: 'running',
    })
  })

  it('never offers to restart an agent on a retired runtime', () => {
    const stopped = { status: 'unavailable', provider: 'null', lifecycle: 'terminated' } as const
    expect(agentRestartable(onRuntime(ok, stopped))).toBe(true)
    expect(agentRestartable(onRuntime({ ...ok, status: 'retired' }, stopped))).toBe(false)
  })

  it('knows a beta runtime', () => {
    expect(isBetaRuntime(onRuntime({ ...ok, status: 'beta' }))).toBe(true)
    expect(isBetaRuntime(onRuntime(ok))).toBe(false)
  })
})
