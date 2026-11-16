import { describe, it, expect, afterEach, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@tests/utils'
import { currentUser } from '@/mock/fixtures/users'

const ROUTE = '/acme-analytics/schedules'

describe('AppShell skip link', () => {
  it('is the first tab stop and moves focus into <main>', async () => {
    const user = userEvent.setup()
    renderWithProviders({ initialRoute: ROUTE })

    const skip = await screen.findByRole('link', { name: /skip to main content/i })
    await user.tab()
    expect(skip).toHaveFocus()

    await user.keyboard('{Enter}')
    expect(document.getElementById('main-content')).toHaveFocus()
  })

  it('labels the main landmark target', async () => {
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByRole('link', { name: /skip to main content/i })
    const main = document.getElementById('main-content')
    expect(main?.tagName).toBe('MAIN')
    expect(main).toHaveAttribute('tabindex', '-1')
  })
})

describe('route titles', () => {
  it('names the tab after the page', async () => {
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByRole('heading', { name: 'Schedules', level: 1 })
    expect(document.title).toBe('Schedules · DuckHaven')
  })
})

describe('navigation shortcut', () => {
  async function renderShell() {
    renderWithProviders({ initialRoute: ROUTE })
    const nav = await screen.findByRole('navigation', { name: 'Main navigation' })
    await screen.findByRole('heading', { name: 'Schedules', level: 1 })
    return nav
  }

  it('collapses and expands the navigation with Cmd+B or Ctrl+B, saving it on the account', async () => {
    const nav = await renderShell()

    await userEvent.keyboard('{Control>}b{/Control}')
    expect(nav).toHaveAttribute('data-mode', 'collapsed')
    await waitFor(() => expect(currentUser().sidebar).toBe('collapsed'))

    await userEvent.keyboard('{Meta>}b{/Meta}')
    expect(nav).toHaveAttribute('data-mode', 'expanded')
    await waitFor(() => expect(currentUser().sidebar).toBe('expanded'))
  })

  it('leaves Ctrl+Shift+B alone', async () => {
    const nav = await renderShell()

    await userEvent.keyboard('{Control>}{Shift>}b{/Shift}{/Control}')

    expect(nav).toHaveAttribute('data-mode', 'expanded')
  })
})

describe('navigation on a phone', () => {
  function phoneWidth() {
    vi.mocked(window.matchMedia).mockImplementation(
      (query: string) =>
        ({
          matches: query === '(max-width: 767px)',
          media: query,
          onchange: null,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
          dispatchEvent: vi.fn(),
        }) as unknown as MediaQueryList,
    )
  }

  afterEach(() => {
    vi.mocked(window.matchMedia).mockImplementation(
      (query: string) =>
        ({
          matches: false,
          media: query,
          onchange: null,
          addEventListener: vi.fn(),
          removeEventListener: vi.fn(),
          dispatchEvent: vi.fn(),
        }) as unknown as MediaQueryList,
    )
  })

  it('is a drawer that closes once a page is chosen', async () => {
    phoneWidth()
    const user = userEvent.setup()
    const { router } = renderWithProviders({ initialRoute: ROUTE })
    await screen.findByRole('heading', { name: 'Schedules', level: 1 })
    expect(screen.queryByRole('navigation', { name: 'Main navigation' })).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Open navigation' }))
    const drawer = await screen.findByRole('dialog', { name: 'Navigation' })
    await user.click(within(drawer).getByRole('link', { name: 'History' }))

    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/acme-analytics/history'),
    )
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Navigation' })).toBeNull())
  })

  it('ignores Cmd+B, leaving the saved layout as it was', async () => {
    phoneWidth()
    renderWithProviders({ initialRoute: ROUTE })
    await screen.findByRole('heading', { name: 'Schedules', level: 1 })

    await userEvent.keyboard('{Control>}b{/Control}')

    expect(currentUser().sidebar).toBeNull()
  })
})
