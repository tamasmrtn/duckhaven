import { setupServer } from 'msw/node'
import { authHandlers } from '@/mock/handlers/auth'
import { workspaceHandlers } from '@/mock/handlers/workspaces'
import { agentHandlers } from '@/mock/handlers/agents'
import { schemaHandlers } from '@/mock/handlers/schemas'
import { queryHandlers } from '@/mock/handlers/queries'
import { storageBackendHandlers } from '@/mock/handlers/storage-backends'

export const server = setupServer(
  ...authHandlers,
  ...workspaceHandlers,
  ...agentHandlers,
  ...schemaHandlers,
  ...queryHandlers,
  ...storageBackendHandlers,
)
