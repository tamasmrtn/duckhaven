import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from '@tests/utils'

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
