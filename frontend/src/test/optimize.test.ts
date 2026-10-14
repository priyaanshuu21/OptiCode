import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useCompilerStore } from '@/store/compiler'
import { loadFixture } from './fixtures'

/**
 * The lifecycle of an optimization proposal.
 *
 * Generating a proposal is a *second* compile whose result must never rewrite the editor on its
 * own, and whose apply must be provably about the text the reader is looking at. Those are the two
 * properties these tests pin down: the response cannot overwrite a newer source, and applying
 * writes the exact string the comparison showed.
 */

function reset(): void {
  useCompilerStore.setState({
    source: '',
    compiledSource: null,
    response: null,
    status: 'idle',
    error: null,
    optimizationPreview: null,
    previewStatus: 'idle',
    previewError: null,
  })
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })
}

describe('optimization preview', () => {
  beforeEach(reset)

  it('proposes a rewrite of the current source', async () => {
    const { response, source } = loadFixture('folded')
    useCompilerStore.setState({ source })
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(response)))

    await useCompilerStore.getState().generateOptimizationPreview()

    const preview = useCompilerStore.getState().optimizationPreview
    expect(preview).not.toBeNull()
    expect(preview!.originalSource).toBe(source)
    // The recorded fixture is a first pass: the source generator has a rewrite to offer.
    expect(preview!.changed).toBe(true)
    expect(preview!.optimizedSource).not.toBe(source)
  })

  it('discards a proposal whose source changed while the request was in flight', async () => {
    const { response, source } = loadFixture('folded')
    useCompilerStore.setState({ source })

    let release!: () => void
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        await gate
        return jsonResponse(response)
      }),
    )

    const pending = useCompilerStore.getState().generateOptimizationPreview()
    // The editor moves on before the response arrives. Keeping the proposal would let a later
    // apply write code over text the reader never agreed to replace.
    useCompilerStore.setState({ source: `${source}\n// edited` })
    release()
    await pending

    expect(useCompilerStore.getState().optimizationPreview).toBeNull()
    expect(useCompilerStore.getState().previewStatus).toBe('idle')
  })

  it('records an error and leaves the source alone when the preview compile fails', async () => {
    const { source } = loadFixture('folded')
    useCompilerStore.setState({ source })
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('boom', { status: 500 })),
    )

    await useCompilerStore.getState().generateOptimizationPreview()

    expect(useCompilerStore.getState().optimizationPreview).toBeNull()
    expect(useCompilerStore.getState().previewStatus).toBe('error')
    expect(useCompilerStore.getState().previewError).toBeTruthy()
    // Opening the screen and pressing Optimize must not change the editor on a failure either.
    expect(useCompilerStore.getState().source).toBe(source)
  })

  it('applies exactly the optimized source the proposal showed', async () => {
    const { response, source } = loadFixture('folded')
    useCompilerStore.setState({ source })
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(response)))

    await useCompilerStore.getState().generateOptimizationPreview()
    const preview = useCompilerStore.getState().optimizationPreview!
    useCompilerStore.getState().applyOptimizedSource(preview.optimizedSource)

    expect(useCompilerStore.getState().source).toBe(preview.optimizedSource)
    // The proposal has been consumed, so a second apply has nothing left to write.
    expect(useCompilerStore.getState().optimizationPreview).toBeNull()
  })
})
