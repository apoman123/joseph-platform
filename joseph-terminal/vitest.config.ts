/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Root Vitest configuration with one project per test tier:
 * `packages` and `web` run unit tests in jsdom, `architecture` runs the
 * boundary and contract tests in Node.
 */

import react from '@vitejs/plugin-react';
import {defineConfig} from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    projects: [
      {
        extends: true,
        test: {
          name: 'packages',
          include: ['packages/*/src/**/*.test.{ts,tsx}'],
          environment: 'jsdom',
          setupFiles: ['./tests/setup/dom.ts'],
        },
      },
      {
        extends: true,
        test: {
          name: 'web',
          root: './apps/web',
          include: ['src/**/*.test.{ts,tsx}'],
          environment: 'jsdom',
          setupFiles: ['../../tests/setup/dom.ts'],
        },
      },
      {
        extends: true,
        test: {
          name: 'architecture',
          include: [
            'tests/architecture/**/*.test.ts',
            'packages/contracts/tests/**/*.test.ts',
          ],
          environment: 'node',
        },
      },
    ],
    coverage: {
      provider: 'v8',
      include: ['packages/*/src/**', 'apps/web/src/**'],
      exclude: ['**/*.test.*', '**/generated/**'],
    },
  },
});
