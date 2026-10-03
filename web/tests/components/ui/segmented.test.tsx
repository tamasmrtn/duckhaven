import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Segmented } from '@/components/ui/segmented'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

const OPTIONS = [
  { value: 'a', label: 'Alpha' },
  { value: 'b', label: 'Beta' },
]

describe('Segmented', () => {
  it('marks the chosen option and reports a new choice', async () => {
    const onChange = vi.fn()
    render(<Segmented label="Show" value="a" onChange={onChange} options={OPTIONS} />)

    expect(screen.getByRole('button', { name: 'Alpha' })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(screen.getByRole('button', { name: 'Beta' }))
    expect(onChange).toHaveBeenCalledWith('b')
  })

  // Regression: at 25px with 11px text it sat shorter than the inputs and
  // buttons beside it in every toolbar.
  it('matches the 28px controls beside it', () => {
    render(
      <Segmented label="Show" value="a" onChange={() => {}} options={OPTIONS} />,
    )
    expect(screen.getByRole('group', { name: 'Show' }).className).toContain('h-7')
    expect(screen.getByRole('button', { name: 'Alpha' }).className).toContain('text-xs')
  })
})

describe('Tabs', () => {
  // Regression: tabs were a 40px grey pill bar at 13px, unlike every other set
  // of options in the app.
  it('share the Segmented look', () => {
    render(
      <Tabs defaultValue="a">
        <TabsList aria-label="Views">
          <TabsTrigger value="a">Alpha</TabsTrigger>
          <TabsTrigger value="b">Beta</TabsTrigger>
        </TabsList>
      </Tabs>,
    )
    const list = screen.getByRole('tablist', { name: 'Views' })
    expect(list.className).toContain('h-7')
    expect(list.className).toContain('border')
    expect(list.className).not.toContain('bg-muted')
    const tab = screen.getByRole('tab', { name: 'Alpha' })
    expect(tab.className).toContain('text-xs')
    expect(tab.className).toContain('data-[state=active]:bg-accent')
  })
})
