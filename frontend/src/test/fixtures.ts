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
}

export type FixtureName = 'hello' | 'loops' | 'io' | 'mixed_types' | 'dead_code' | 'error'

const NAMES: FixtureName[] = ['hello', 'loops', 'io', 'mixed_types', 'dead_code', 'error']

export function loadFixture(name: FixtureName): Fixture {
  const raw = readFileSync(join(fixtureDir, `${name}.json`), 'utf8')
  const parsed = JSON.parse(raw) as {
    source: string
    request: CompileRequest | null
    response: CompileResponse
  }
  const mode = parsed.response.mode as 'analyze' | 'optimize'
  const request: CompileRequest = (parsed.request ?? { source: parsed.source, mode }) as CompileRequest
  return { source: parsed.source, request, response: parsed.response }
}

export function loadFixtures(): { name: FixtureName; fixture: Fixture }[] {
  return NAMES.map((name) => ({ name, fixture: loadFixture(name) }))
}

/** The default source, used by the store's tests. */
export function defaultSource(): string {
  return loadFixture('hello').source
}
