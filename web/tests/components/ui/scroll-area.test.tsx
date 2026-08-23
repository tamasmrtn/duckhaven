import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import * as ScrollAreaPrimitive from '@radix-ui/react-scroll-area'
import { ScrollArea } from '@/components/ui/scroll-area'

// jsdom has no layout, so the shadcn wrapper (Radix `type="hover"`) never
// mounts its thumb. These cases render the Radix primitives directly with
// `type="always"` + `forceMount`, duplicating the component's class strings —
// a string-lock that catches renames and rewiring (not class drift).

describe('ScrollArea', () => {
  it('renders its children inside the viewport', () => {
    render(<ScrollArea>Hello thread</ScrollArea>)
    expect(screen.getByText('Hello thread')).toBeInTheDocument()
  })

  it('styles the thumb with the on-brand scrollbar classes', () => {
    const { container } = render(
      <ScrollAreaPrimitive.Root type="always">
        <ScrollAreaPrimitive.Viewport className="h-full w-full">
          <p>content</p>
        </ScrollAreaPrimitive.Viewport>
        <ScrollAreaPrimitive.Scrollbar
          orientation="vertical"
          className="flex touch-none select-none transition-colors h-full w-2.5 border-l border-l-transparent p-[1px]"
        >
          <ScrollAreaPrimitive.Thumb
            forceMount
            className="relative flex-1 rounded-full bg-scrollbar-thumb transition-colors hover:bg-scrollbar-thumb-hover"
          />
        </ScrollAreaPrimitive.Scrollbar>
      </ScrollAreaPrimitive.Root>,
    )

    const thumb = container.querySelector('.bg-scrollbar-thumb')
    expect(thumb).not.toBeNull()
    expect(thumb).toHaveClass(
      'rounded-full',
      'transition-colors',
      'hover:bg-scrollbar-thumb-hover',
    )
  })

  it('keeps the track thin and transparent-backed', () => {
    const { container } = render(
      <ScrollAreaPrimitive.Root type="always">
        <ScrollAreaPrimitive.Viewport className="h-full w-full">
          <p>content</p>
        </ScrollAreaPrimitive.Viewport>
        <ScrollAreaPrimitive.Scrollbar
          orientation="vertical"
          className="flex touch-none select-none transition-colors h-full w-2.5 border-l border-l-transparent p-[1px]"
        >
          <ScrollAreaPrimitive.Thumb
            forceMount
            className="relative flex-1 rounded-full bg-scrollbar-thumb"
          />
        </ScrollAreaPrimitive.Scrollbar>
      </ScrollAreaPrimitive.Root>,
    )

    const track = container
      .querySelector('.bg-scrollbar-thumb')
      ?.closest('[data-orientation="vertical"]')
    expect(track).not.toBeNull()
    expect(track).toHaveClass('w-2.5', 'border-l-transparent', 'transition-colors')
  })
})
