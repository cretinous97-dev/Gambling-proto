/**
 * ESLint config.
 *
 * Deliberately small: the rules that catch bugs, not the ones that start
 * arguments. Two things earn their place here -
 *
 *   1. `react-hooks` rules, because a missing dependency array entry or a hook
 *      called conditionally is a real defect that only shows up at runtime,
 *      often only on the page you did not reload.
 *   2. `no-unused-vars` with an underscore escape hatch, because an import left
 *      behind after a refactor is how a stale module keeps being bundled.
 *
 * Formatting is not enforced: there is no prettier run in CI, and a build that
 * fails over quote style teaches people to ignore the build.
 */
import js from '@eslint/js'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import globals from 'globals'

export default [
  { ignores: ['dist/**', 'node_modules/**', 'public/**'] },
  js.configs.recommended,
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: { react, 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Without this, every component that is only referenced from JSX
      // (`<Sheet>`, `<Routes>`) is reported as an unused variable. It is off
      // in the recommended preset because of legacy JSX transforms; here it
      // is the difference between a useful linter and 180 false errors.
      'react/jsx-uses-vars': 'error',
      'no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_[a-z]', caughtErrors: 'none' },
      ],
      // React 18 + the automatic JSX runtime: `React` does not need importing,
      // and `no-undef` cannot see JSX-only identifiers.
      'no-undef': 'off',
    },
  },
  {
    files: ['scripts/**/*.mjs'],
    languageOptions: { globals: globals.node },
  },
]
