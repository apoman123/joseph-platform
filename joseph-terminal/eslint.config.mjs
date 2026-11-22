/**
 * @fileoverview ESLint flat config approximating the Google TypeScript
 * Style Guide: named exports only, no `any`, interfaces for object shapes,
 * TypeScript `private` instead of `#private`, CONSTANT_CASE module constants
 * and JSDoc on every class, interface, type, enum, function and method.
 */
import js from '@eslint/js';
import jsdoc from 'eslint-plugin-jsdoc';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

const JSDOC_CONTEXTS = [
  'TSInterfaceDeclaration',
  'TSTypeAliasDeclaration',
  'TSEnumDeclaration',
  'TSMethodSignature',
  'Program > VariableDeclaration > VariableDeclarator > ArrowFunctionExpression',
  'ExportNamedDeclaration > VariableDeclaration > VariableDeclarator > ArrowFunctionExpression',
];

export default tseslint.config(
  {
    ignores: [
      '**/node_modules/**',
      '**/.next/**',
      '**/coverage/**',
      '**/playwright-report/**',
      '**/test-results/**',
      '**/generated/**',
      'apps/web/next-env.d.ts',
      '.dependency-cruiser.cjs',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    plugins: {jsdoc, 'react-hooks': reactHooks},
    settings: {
      jsdoc: {
        mode: 'typescript',
        tagNamePreference: {returns: 'return', file: 'fileoverview'},
      },
    },
    rules: {
      'no-var': 'error',
      'prefer-const': 'error',
      eqeqeq: ['error', 'always', {null: 'ignore'}],
      'no-restricted-syntax': [
        'error',
        {
          selector: 'ExportDefaultDeclaration',
          message: 'Google TS style: use named exports.',
        },
        {
          selector: 'PropertyDefinition > PrivateIdentifier',
          message: 'Google TS style: use the `private` modifier, not #private.',
        },
        {
          selector: 'TSEnumDeclaration[const=true]',
          message: 'Google TS style: no const enum.',
        },
        {
          selector: 'TSModuleDeclaration[kind="namespace"]',
          message: 'Google TS style: no namespaces.',
        },
      ],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-non-null-assertion': 'error',
      '@typescript-eslint/consistent-type-definitions': ['error', 'interface'],
      '@typescript-eslint/consistent-type-imports': [
        'error',
        {fixStyle: 'inline-type-imports'},
      ],
      '@typescript-eslint/explicit-module-boundary-types': 'error',
      '@typescript-eslint/no-unused-vars': [
        'error',
        {argsIgnorePattern: '^_', varsIgnorePattern: '^_'},
      ],
      '@typescript-eslint/naming-convention': [
        'error',
        {
          selector: 'default',
          format: ['camelCase'],
          leadingUnderscore: 'allow',
        },
        {
          selector: 'variable',
          format: ['camelCase', 'UPPER_CASE', 'PascalCase'],
          leadingUnderscore: 'allow',
        },
        {selector: 'variable', modifiers: ['destructured'], format: null},
        {selector: 'function', format: ['camelCase', 'PascalCase']},
        {
          selector: 'parameter',
          format: ['camelCase'],
          leadingUnderscore: 'allow',
        },
        {selector: 'parameter', modifiers: ['destructured'], format: null},
        {selector: 'typeLike', format: ['PascalCase']},
        {selector: 'enumMember', format: ['UPPER_CASE']},
        {
          selector: 'classProperty',
          modifiers: ['static', 'readonly'],
          format: ['UPPER_CASE', 'camelCase'],
        },
        {
          selector: [
            'objectLiteralProperty',
            'typeProperty',
            'objectLiteralMethod',
            'typeMethod',
          ],
          format: null,
        },
        {selector: 'import', format: null},
      ],
      'jsdoc/require-jsdoc': [
        'error',
        {
          publicOnly: false,
          require: {
            FunctionDeclaration: true,
            ClassDeclaration: true,
            MethodDefinition: true,
          },
          contexts: JSDOC_CONTEXTS,
          checkConstructors: false,
          checkGetters: true,
          checkSetters: true,
        },
      ],
      'jsdoc/require-description': [
        'error',
        {contexts: ['any'], exemptedBy: ['fileoverview', 'inheritdoc', 'type']},
      ],
      'jsdoc/require-param': [
        'error',
        {
          checkDestructured: false,
          checkDestructuredRoots: false,
          contexts: JSDOC_CONTEXTS.concat([
            'FunctionDeclaration',
            'MethodDefinition',
          ]),
        },
      ],
      'jsdoc/require-param-description': 'error',
      'jsdoc/check-param-names': ['error', {checkDestructured: false}],
      'jsdoc/require-returns': [
        'error',
        {
          checkGetters: false,
          forceReturnsWithAsync: false,
          contexts: [
            'FunctionDeclaration',
            'MethodDefinition',
            'Program > VariableDeclaration > VariableDeclarator > ArrowFunctionExpression',
            'ExportNamedDeclaration > VariableDeclaration > VariableDeclarator > ArrowFunctionExpression',
          ],
        },
      ],
      'jsdoc/require-returns-description': 'error',
      'jsdoc/no-types': 'error',
      'jsdoc/check-tag-names': 'error',
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
    },
  },
  {
    // Framework entry points require default exports.
    files: [
      'apps/web/src/app/**/{page,layout,not-found,error,global-error,loading}.tsx',
      '**/*.config.{ts,mjs,js}',
      '**/playwright.config.ts',
    ],
    rules: {'no-restricted-syntax': 'off'},
  },
  {
    files: ['**/*.{js,mjs,cjs}'],
    languageOptions: {
      globals: {process: 'readonly', console: 'readonly', URL: 'readonly'},
    },
  }
);
