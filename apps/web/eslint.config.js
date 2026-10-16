import js from '@eslint/js'
import prettier from 'eslint-config-prettier'
import reactHooks from 'eslint-plugin-react-hooks'
import { reactRefresh } from 'eslint-plugin-react-refresh'
import globals from 'globals'
import { defineConfig } from 'eslint/config'
import tseslint from 'typescript-eslint'

export default defineConfig(
  { ignores: ['dist', 'coverage', 'playwright-report', 'test-results', 'src/api/schema.ts'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  reactRefresh.configs.vite(),
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: { globals: globals.browser },
    rules: {
      // CLAUDE.md: no `any` without a comment explaining why.
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
  { files: ['vite.config.ts', 'eslint.config.js'], languageOptions: { globals: globals.node } },
  prettier, // must be last: turns off rules that conflict with Prettier formatting
)
