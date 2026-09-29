/**
 * Validate vercel.json against Vercel's own schema, locally.
 *
 * Vercel validates the config before building and fails the whole deployment on
 * a schema violation. That is how this bit us:
 *
 *     functions.api/[...path].py.includeFiles should be string
 *
 * The runtime accepts an array there, but the published schema does not - so
 * only the schema matters, and the schema is the thing nobody checks.
 *
 * This uses the same compiled validator the CLI ships, so a pass here means a
 * pass at deploy time. `vercel` is not a dependency of this project (it would
 * slow every build); install it on demand:
 *
 *     cd frontend
 *     npm install --no-save vercel
 *     node scripts/validate-vercel-config.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const configPath = join(here, '..', '..', 'vercel.json')

let getConfigValidator
try {
  ;({ getConfigValidator } = await import('vercel/dist/chunks/config-validator.mjs'))
} catch {
  console.error(
    'The `vercel` package is not installed.\n' +
      'Run:  cd frontend && npm install --no-save vercel\n' +
      'then: node scripts/validate-vercel-config.mjs',
  )
  process.exit(2)
}

const config = JSON.parse(readFileSync(configPath, 'utf8'))
const validate = getConfigValidator()

if (validate(config)) {
  console.log(`vercel.json is valid (${Object.keys(config).length} top-level keys)`)
  process.exit(0)
}

console.error('vercel.json FAILED schema validation - the deployment would be rejected:\n')
for (const error of validate.errors ?? []) {
  const path = error.instancePath || '/'
  const hint = error.params?.allowedValue ? ` (allowed: ${error.params.allowedValue})` : ''
  console.error(`  ${path} ${error.message}${hint}`)
}
console.error(
  '\nNote: several keys accept a STRING where you might expect an array, ' +
    'includeFiles among them.',
)
process.exit(1)
