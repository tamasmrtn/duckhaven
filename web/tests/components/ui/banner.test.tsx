import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Banner } from '@/components/ui/banner'

describe('Banner', () => {
  it('renders its children inside a note region', () => {
    render(<Banner>Heads up</Banner>)
    const note = screen.getByRole('note')
    expect(note).toHaveTextContent('Heads up')
  })
})
