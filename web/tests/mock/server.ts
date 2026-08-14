import { setupServer } from 'msw/node'
import { authHandlers } from '@/mock/handlers/auth'
import { setupHandlers } from '@/mock/handlers/setup'
import { workspaceHandlers } from '@/mock/handlers/workspaces'
import { catalogHandlers } from '@/mock/handlers/catalogs'
import { agentHandlers } from '@/mock/handlers/agents'
import { metricsHandlers } from '@/mock/handlers/metrics'
import { schemaHandlers } from '@/mock/handlers/schemas'
import { queryHandlers } from '@/mock/handlers/queries'
import { scheduleHandlers } from '@/mock/handlers/schedules'
import { sqlSessionHandlers } from '@/mock/handlers/sql-sessions'
import { assistantHandlers } from '@/mock/handlers/assistant'
import { storageBackendHandlers } from '@/mock/handlers/storage-backends'
import { catalogMigrationHandlers } from '@/mock/handlers/catalog-migrations'
import { userHandlers } from '@/mock/handlers/users'
import { serviceAccountHandlers } from '@/mock/handlers/service-accounts'
import { maintenanceHandlers } from '@/mock/handlers/maintenance'
import { lineageHandlers } from '@/mock/handlers/lineage'

export const server = setupServer(
  ...authHandlers,
  ...setupHandlers,
  ...workspaceHandlers,
  ...catalogHandlers,
  ...agentHandlers,
  ...metricsHandlers,
  ...schemaHandlers,
  ...queryHandlers,
  ...scheduleHandlers,
  ...sqlSessionHandlers,
  ...assistantHandlers,
  ...storageBackendHandlers,
  ...catalogMigrationHandlers,
  ...userHandlers,
  ...serviceAccountHandlers,
  ...maintenanceHandlers,
  ...lineageHandlers,
)
