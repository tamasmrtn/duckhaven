import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SqlEditor } from '@/features/worksheet/SqlEditor'

const { ready } = vi.hoisted(() => ({ ready: { value: false } }))

vi.mock('@/features/worksheet/monacoLoader', () => ({
  useMonacoReady: () => ready.value,
}))
vi.mock('@monaco-editor/react', () => ({
  Editor: () => <div data-testid="monaco-editor" />,
}))

describe('SqlEditor', () => {
  // Mounting <Editor> before the bundled Monaco is configured would make
  // @monaco-editor/react fetch Monaco from its CDN instead.
  it('waits for the bundled Monaco before mounting the editor', () => {
    ready.value = false

    render(<SqlEditor value="SELECT 1" onChange={() => undefined} />)

    expect(screen.getByText('Loading editor…')).toBeInTheDocument()
    expect(screen.queryByTestId('monaco-editor')).not.toBeInTheDocument()
  })

  it('mounts the editor once Monaco is ready', () => {
    ready.value = true

    render(<SqlEditor value="SELECT 1" onChange={() => undefined} />)

    expect(screen.getByTestId('monaco-editor')).toBeInTheDocument()
  })
})
