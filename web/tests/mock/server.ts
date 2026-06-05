import { setupServer } from 'msw/node'
import { authHandlers } from '@/mock/handlers/auth'
import { setupHandlers } from '@/mock/handlers/setup'
import { workspaceHandlers } from '@/mock/handlers/workspaces'
import { agentHandlers } from '@/mock/handlers/agents'
import { metricsHandlers } from '@/mock/handlers/metrics'
import { schemaHandlers } from '@/mock/handlers/schemas'
import { queryHandlers } from '@/mock/handlers/queries'
import { storageBackendHandlers } from '@/mock/handlers/storage-backends'
import { userHandlers } from '@/mock/handlers/users'

export const server = setupServer(
  ...authHandlers,
  ...setupHandlers,
  ...workspaceHandlers,
  ...agentHandlers,
  ...metricsHandlers,
  ...schemaHandlers,
  ...queryHandlers,
  ...storageBackendHandlers,
  ...userHandlers,
)
