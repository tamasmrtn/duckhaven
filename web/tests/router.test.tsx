import { afterEach, describe, it, expect } from 'vitest'
import { waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '@tests/mock/server'
import { renderWithProviders } from '@tests/utils'

afterEach(() => sessionStorage.clear())

describe('index route ("/")', () => {
  it('opens the page kept before single sign-on, once', async () => {
    sessionStorage.setItem('dh-return-to', '/acme-analytics/dashboards')
    const { router } = renderWithProviders({ initialRoute: '/' })
    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/acme-analytics/dashboards'),
    )
    expect(sessionStorage.getItem('dh-return-to')).toBeNull()
  })

  it('sends an authenticated user to their first workspace (SSO landing)', async () => {
    // The OIDC callback redirects to "/"; an authenticated user must resolve to
    // a workspace, not bounce to /login.
    const { router } = renderWithProviders({ initialRoute: '/' })
    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/acme-analytics/worksheets'),
    )
  })

  it('sends an authenticated user with no workspaces to /welcome', async () => {
    server.use(http.get('/api/workspaces', () => HttpResponse.json([])))
    const { router } = renderWithProviders({ initialRoute: '/' })
    await waitFor(() => expect(router.state.location.pathname).toBe('/welcome'))
  })

  it('sends an unauthenticated user to /login', async () => {
    server.use(
      http.get('/api/me', () => new HttpResponse(null, { status: 401 })),
    )
    const { router } = renderWithProviders({ initialRoute: '/' })
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
  })
})
