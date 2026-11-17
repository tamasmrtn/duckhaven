import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SqlEditor } from '@/features/worksheet/SqlEditor'

const { ready, mounted } = vi.hoisted(() => ({
  ready: { value: false },
  mounted: { onMount: null as null | ((editor: never, monaco: never) => void) },
}))

vi.mock('@/features/worksheet/monacoLoader', () => ({
  useMonacoReady: () => ready.value,
}))
vi.mock('@monaco-editor/react', () => ({
  Editor: (props: { onMount: (editor: never, monaco: never) => void }) => {
    mounted.onMount = props.onMount
    return <div data-testid="monaco-editor" />
  },
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

  it('inserts a table dropped on the editor after the worksheet tab changes', () => {
    ready.value = true
    render(<SqlEditor value="" onChange={() => undefined} />)

    // Monaco renders its view inside the container it was created in, and
    // replaces that view node whenever the model changes.
    const container = document.createElement('div')
    let view = document.createElement('div')
    container.append(view)
    document.body.append(container)
    const executeEdits = vi.fn()
    const editor = {
      addCommand: vi.fn(),
      getDomNode: () => view,
      getContainerDomNode: () => container,
      getTargetAtClientPoint: () => ({ position: { lineNumber: 1, column: 8 } }),
      getPosition: () => null,
      executeEdits,
      focus: vi.fn(),
    }
    const monaco = {
      KeyMod: { CtrlCmd: 0, Shift: 0, Alt: 0 },
      KeyCode: { KeyS: 0, KeyF: 0, Enter: 0 },
      Range: class {
        constructor(...args: number[]) {
          Object.assign(this, { args })
        }
      },
    }
    mounted.onMount?.(editor as never, monaco as never)

    // Switching to another worksheet tab sets another model.
    view.remove()
    view = document.createElement('div')
    container.append(view)

    const drop = Object.assign(new Event('drop', { bubbles: true, cancelable: true }), {
      dataTransfer: { getData: () => 'lake.sales.orders' },
      clientX: 0,
      clientY: 0,
    })
    view.dispatchEvent(drop)

    expect(drop.defaultPrevented).toBe(true)
    expect(executeEdits).toHaveBeenCalledWith('drag-drop-table', [
      expect.objectContaining({ text: 'lake.sales.orders' }),
    ])
    container.remove()
  })
})
