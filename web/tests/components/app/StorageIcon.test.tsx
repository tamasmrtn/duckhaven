import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StorageIcon, StorageLabel } from '@/components/app/StorageIcon'

describe('StorageIcon', () => {
  it('renders Local FS aria-label for local_fs', () => {
    render(<StorageIcon kind="local_fs" />)
    expect(screen.getByLabelText('Local FS')).toBeInTheDocument()
  })

  it('renders NAS aria-label for nas', () => {
    render(<StorageIcon kind="nas" />)
    expect(screen.getByLabelText('NAS')).toBeInTheDocument()
  })

  it('renders S3 aria-label for s3', () => {
    render(<StorageIcon kind="s3" />)
    expect(screen.getByLabelText('S3')).toBeInTheDocument()
  })

  it('renders ADLS aria-label for adls_gen2', () => {
    render(<StorageIcon kind="adls_gen2" />)
    expect(screen.getByLabelText('ADLS')).toBeInTheDocument()
  })
})

describe('StorageLabel', () => {
  it('renders the correct label text', () => {
    render(<StorageLabel kind="s3" />)
    expect(screen.getByText('S3')).toBeInTheDocument()
  })
})
