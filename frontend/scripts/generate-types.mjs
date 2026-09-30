/**
 * Generate TypeScript types from the backend's OpenAPI document.
 *
 * Run `python backend/scripts/generate_openapi.py` first if the API has changed. The output is
 * committed, because a frontend that cannot be checked out and built without a Python
 * interpreter is a frontend nobody but the author can run.
 */
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const specPath = resolve(root, 'openapi.json')
const outPath = resolve(root, 'src/api/schema.d.ts')

if (!existsSync(specPath)) {
  console.error(`Missing ${specPath}. Run: python ../backend/scripts/generate_openapi.py`)
  process.exit(1)
}

// The banner is generated rather than hand-written so that someone editing this file by hand
// notices immediately that their change is about to be overwritten.
const banner = [
  '/**',
  ' * Generated from the backend OpenAPI document by `npm run gen:types`. Do not edit.',
  ' *',
  ' * Regenerate with:',
  ' *   python ../backend/scripts/generate_openapi.py',
  ' *   npm run gen:types',
  ' */',
  '',
].join('\n')

const generated = execFileSync(
  process.execPath,
  [
    resolve(root, 'node_modules/openapi-typescript/bin/cli.js'),
    specPath,
    '--alphabetize',
  ],
  { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 },
)

mkdirSync(dirname(outPath), { recursive: true })
const previous = existsSync(outPath) ? readFileSync(outPath, 'utf8') : ''
writeFileSync(outPath, banner + generated, 'utf8')

const unchanged = previous === banner + generated
console.log(
  unchanged
    ? `No change: ${outPath}`
    : `Wrote ${outPath} (${generated.split('\n').length} lines)`,
)
