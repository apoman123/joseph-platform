/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Module boundary rules for the public terminal (V5 §10
 * "Architecture tests must fail when ...").
 */

/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: 'no-circular',
      severity: 'error',
      comment:
        'Circular dependencies make features impossible to remove independently.',
      from: {},
      to: {circular: true},
    },
    {
      name: 'chart-library-only-in-adapter',
      severity: 'error',
      comment:
        'Only packages/chart-core/src/adapters/lwc may import lightweight-charts; everything else uses ChartPort.',
      from: {pathNot: '^packages/chart-core/src/adapters/lwc/'},
      to: {path: '(^|/)lightweight-charts(/|$)'},
    },
    {
      name: 'no-unresolvable-imports',
      severity: 'error',
      comment:
        'Every import must resolve; undeclared dependencies break isolated installs.',
      from: {pathNot: 'next-env\\.d\\.ts$'},
      to: {couldNotResolve: true},
    },
    {
      name: 'packages-never-import-apps',
      severity: 'error',
      from: {path: '^packages/'},
      to: {path: '^apps/'},
    },
    {
      name: 'drawing-tools-are-independent',
      severity: 'error',
      comment:
        'A drawing tool must never import another tool (removal safety).',
      from: {path: '^packages/drawing-tools/src/([^/]+)/'},
      to: {
        path: '^packages/drawing-tools/src/[^/]+/',
        pathNot: [
          '^packages/drawing-tools/src/$1/',
          '^packages/drawing-tools/src/testing/',
        ],
      },
    },
    {
      name: 'indicator-viz-is-render-only',
      severity: 'error',
      comment:
        'Indicator visualization never depends on trading or strategy logic.',
      from: {path: '^packages/indicator-viz/'},
      to: {path: '^packages/(chart-trading|strategy-builder|mock-provider)/'},
    },
    {
      name: 'packages-are-framework-free',
      severity: 'error',
      comment:
        'Packages stay framework-free; React and Next.js live in apps/web.',
      from: {path: '^packages/'},
      to: {path: 'node_modules/(react|react-dom|next)/'},
    },
    {
      name: 'no-node-builtins-in-browser-code',
      severity: 'error',
      comment:
        'Browser code must not import Node built-ins (fs, crypto, child_process, ...).',
      from: {
        path: '^(packages/[^/]+/src|apps/web/src)/',
        pathNot: '(\\.test\\.tsx?$|/testing/|\\.d\\.ts$)',
      },
      to: {dependencyTypes: ['core']},
    },
    {
      name: 'no-private-engine-code',
      severity: 'error',
      comment:
        'The public terminal never imports the private Python engines or their files.',
      from: {},
      to: {path: 'joseph-(market|execution)-engine'},
    },
    {
      name: 'no-signing-or-credential-modules',
      severity: 'error',
      comment:
        'No browser module may import signing, credential or secret handling code.',
      from: {path: '^(packages|apps/web/src)/'},
      to: {
        path: '(signing|signer|credential|secret|wallet-key|private-key)',
        pathNot: 'node_modules/lucide-react',
      },
    },
    {
      name: 'features-use-chart-port',
      severity: 'error',
      comment:
        'Features mutate charts only through ChartPort/overlay layers, never the chart library or adapter.',
      from: {path: '^apps/web/src/features/'},
      to: {path: '(lightweight-charts|packages/chart-core/src/adapters)'},
    },
    {
      name: 'no-orphans-in-packages',
      severity: 'warn',
      from: {
        orphan: true,
        path: '^packages/[^/]+/src/',
        pathNot: '(\\.test\\.tsx?$|\\.d\\.ts$|/index\\.ts$)',
      },
      to: {},
    },
  ],
  options: {
    doNotFollow: {path: 'node_modules'},
    exclude: {
      path: '(\\.next|\\.next-e2e|node_modules/\\.cache|coverage|playwright-report|test-results)',
    },
    tsPreCompilationDeps: true,
    tsConfig: {fileName: 'tsconfig.base.json'},
    preserveSymlinks: false,
    enhancedResolveOptions: {
      exportsFields: ['exports'],
      conditionNames: ['import', 'require', 'node', 'default', 'types'],
      extensions: ['.ts', '.tsx', '.js', '.mjs', '.cjs', '.json'],
      mainFields: ['module', 'main', 'types'],
    },
    reporterOptions: {text: {highlightFocused: true}},
  },
};
