import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectTrigger, SelectValue } from '@/components/ui/select'

// Every control in the app is 28px. The primitives default to it, so a page
// that adds no size of its own (Settings, dialogs) matches the rest.
describe('control primitives', () => {
  it('default to 28px', () => {
    render(
      <>
        <Button>Save</Button>
        <Button size="sm">Small</Button>
        <Button size="icon" aria-label="Refresh" />
        <Input aria-label="Name" />
        <Select>
          <SelectTrigger aria-label="Frequency">
            <SelectValue />
          </SelectTrigger>
        </Select>
      </>,
    )
    expect(screen.getByRole('button', { name: 'Save' })).toHaveClass('h-7')
    expect(screen.getByRole('button', { name: 'Small' })).toHaveClass('h-7')
    expect(screen.getByRole('button', { name: 'Refresh' })).toHaveClass('size-7')
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveClass('h-7')
    expect(screen.getByRole('combobox', { name: 'Frequency' })).toHaveClass('h-7')
  })
})
