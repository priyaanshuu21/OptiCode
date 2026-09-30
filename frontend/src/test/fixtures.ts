import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import type { CompileRequest, CompileResponse } from '@/api/types'

/**
 * The recorded responses in `fixtures/`, produced by `backend/scripts/generate_fixtures.py`.
 *
 * They are the backend's own output, byte for byte, which is the point: a test that mounts a
 * view against a hand-written payload only proves the view works against the shape somebody
 * remembered. Re-running the generator is how these stay honest when the API changes.
 */

const here = dirname(fileURLToPath(import.meta.url))
const fixtureDir = join(here, 'fixtures')

export interface Fixture {
  source: string
  request: CompileRequest
  response: CompileResponse
  /** The stdin the fixture was recorded with, so a replay sends the same input. */
  stdin: string
}

export type FixtureName =
  | 'hello'
  | 'loops'
  | 'io'
  | 'mixed_types'
  | 'dead_code'
  | 'unreachable'
  | 'folded'
  | 'error'

/**
 * Every case, in pipeline order.
 *
 * The list is the coverage argument: each of these produces at least one payload element none of
 * the others do — `unreachable` has conditionally unreachable blocks, `folded` has a real
 * before/after rewrite, `error` is a rejected program — so together they reach every branch of
 * every view. Adding a view without a fixture that exercises it is how a view ships broken.
 */
const NAMES: FixtureName[] = [
  'hello',
  'loops',
  'io',
  'mixed_types',
  'dead_code',
  'unreachable',
  'folded',
  'error',
]

export function loadFixture(name: FixtureName): Fixture {
  const raw = readFileSync(join(fixtureDir, `${name}.json`), 'utf8')
  const parsed = JSON.parse(raw) as {
    source: string
    mode: string
    stdin: string
    request: CompileRequest | null
    response: CompileResponse
  }
  const mode = (parsed.response.mode ?? parsed.mode) as 'analyze' | 'optimize'
  // The generator records what it sent, so the request is rebuilt from the response's own mode
  // rather than from a second copy of it that could drift.
  const request: CompileRequest = (parsed.request ?? {
    source: parsed.source,
    mode,
    stdin: parsed.stdin || null,
  }) as CompileRequest
  return { source: parsed.source, request, response: parsed.response, stdin: parsed.stdin ?? '' }
}

export function loadFixtures(): { name: FixtureName; fixture: Fixture }[] {
  return NAMES.map((name) => ({ name, fixture: loadFixture(name) }))
}

/** The default source, used by the store's tests. */
export function defaultSource(): string {
  return loadFixture('hello').source
}
