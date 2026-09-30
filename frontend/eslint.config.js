import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

/**
 * Lint config.
 *
 * Deliberately small. The rules here are the ones that catch mistakes the type checker cannot:
 * hook dependency arrays, and the shadowing that makes `noUnusedLocals` useless. Format and
 * style are not enforced, because a formatter that disagrees with the editor is a nuisance
 * rather than a safeguard.
 */
export default tseslint.config(
  { ignores: ['dist', 'coverage', 'node_modules', 'src/api/schema.d.ts', 'openapi.json'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser },
    },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // The generated OpenAPI types are not ours to annotate.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-unused-vars': [
        'error',
        // An underscore prefix means "deliberately unused", which is the convention the
        // codebase uses for destructured omissions.
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // `any` from generated code is unavoidable; `unknown` plus a guard is the alternative.
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      eqeqeq: ['error', 'smart'],
    },
  },
  {
    // Node-side tooling: the Vite config and the OpenAPI type generator.
    files: ['vite.config.ts', 'scripts/**/*.mjs'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
  {
    files: ['src/test/**/*.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
)
