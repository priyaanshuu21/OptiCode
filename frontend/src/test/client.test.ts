import { afterEach, describe, expect, it, vi } from 'vitest'

import { compile, codegenOf, cfgOf, dataflowOf, irOf, stageRan, stageSkipped, tokensOf, ApiError } from '@/api/client'
import type { CompileResponse } from '@/api/types'
import { loadFixture, loadFixtures } from './fixtures'

/**
 * The API client, against real recorded responses.
 *
 * These tests exist because the client's whole job is to turn a bag of possibly-null payloads
 * into either a narrowed value or a loud failure. A test that only checks the happy path would
 * pass while every skipped stage silently rendered an empty table.
 */

describe('fixture loader', () => {
  it('loads every fixture as a recorded compile response', () => {
    const all = loadFixtures()
    expect(all).toHaveLength(6)
    for (const { name, fixture } of all) {
      expect(fixture.source, name).toBeTypeOf('string')
      expect(fixture.request.mode, name).toMatch(/analyze|optimize/)
      expect(fixture.response.stages_run, name).toContain('tokens')
    }
  })
})

describe('payload narrowing', () => {
  it('returns typed payloads for a successful analyze response', () => {
    const { response } = loadFixture('hello')

    const tokens = tokensOf(response)
    expect(tokens).not.toBeNull()
    expect(tokens?.[0]).toMatchObject({ type: expect.any(String), category: expect.any(String) })

    const ast = response.ast
    expect(ast?.kind).toBeTypeOf('string')
    expect(Array.isArray(ast?.children)).toBe(true)

    const ir = irOf(response)
    expect(ir?.functions.length).toBeGreaterThan(0)
    expect(ir?.instruction_count).toBeGreaterThan(0)
    expect(ir?.text).toContain('t')

    const cfg = cfgOf(response)
    expect(Object.keys(cfg ?? {})).toContain('main')
    const main = cfg?.main
    expect(main?.blocks.length).toBeGreaterThan(0)
    expect(main?.entry).toBeTypeOf('string')

    const dataflow = dataflowOf(response)
    expect(Object.keys(dataflow ?? {})).toContain('main')
  })

  it('narrows an optimize response to a codegen payload', () => {
    const { response } = loadFixture('dead_code')

    expect(response.mode).toBe('optimize')
    const codegen = codegenOf(response)
    expect(codegen).not.toBeNull()
    expect(codegen?.source).toBeTypeOf('string')
    expect(codegen?.applied).toBeTypeOf('boolean')
  })

  it('returns null for a payload the pipeline did not produce', () => {
    // The error fixture fails in the semantic stage: lexing and parsing still happened, but
    // everything from lowering onward was skipped and its payload is null.
    const { response } = loadFixture('error')

    expect(response.stages_run).toContain('tokens')
    expect(tokensOf(response)?.length).toBeGreaterThan(0)

    expect(irOf(response)).toBeNull()
    expect(cfgOf(response)).toBeNull()
    expect(dataflowOf(response)).toBeNull()
    expect(codegenOf(response)).toBeNull()
  })
})

describe('stage bookkeeping', () => {
  it('distinguishes ran from skipped', () => {
    const ok = loadFixture('hello').response
    const bad = loadFixture('error').response

    expect(stageRan(ok, 'cfg')).toBe(true)
    expect(stageSkipped(ok, 'cfg')).toBe(false)

    // The error fixture stops before lowering, so those stages must be recorded as skipped
    // rather than simply absent.
    expect(stageRan(bad, 'ir')).toBe(false)
    expect(bad.stages_skipped.length).toBeGreaterThan(0)
  })
})

describe('compile', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts the request and returns the response body', async () => {
    const { request, response } = loadFixture('hello')
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify(response), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const result = await compile(request)

    expect(result.stages_run).toContain('tokens')
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/compile')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toMatchObject({ mode: request.mode })
  })

  it('reports a backend error with the status code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('boom', { status: 500 })),
    )

    await expect(compile({ source: 'int main() {}', mode: 'analyze' })).rejects.toThrow(ApiError)
  })

  it('explains an unreachable backend instead of surfacing a raw fetch failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )

    await expect(compile({ source: '', mode: 'analyze' })).rejects.toThrow(/backend running/)
  })

  it('rejects a non-object body rather than handing a view nonsense', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('null', { status: 200 })))

    await expect(compile({ source: '', mode: 'analyze' })).rejects.toThrow(/non-object/)
  })
})

describe('payload guards', () => {
  it('throws a PayloadError when a payload has the wrong shape', () => {
    const broken: CompileResponse = {
      success: true,
      mode: 'analyze',
      diagnostics: [],
      tokens: 'not an array' as unknown as CompileResponse['tokens'],
      timings: {},
      stages_run: [],
      stages_skipped: [],
    }

    // The client casts the body without validation, so the guard is the thing that has to catch
    // this. It is a `PayloadError`, not an `ApiError`: the request succeeded, the body did not.
    expect(() => tokensOf(broken)).toThrow(/Expected tokens/)
  })
})
