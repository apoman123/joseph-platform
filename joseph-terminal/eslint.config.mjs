/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview ESLint flat config implementing the Google TypeScript Style
 * Guide (and the JavaScript guide for `.mjs`/`.cjs` scripts and configs).
 *
 * Formatting is Prettier's job (`pnpm format:check`); this config checks
 * everything else that a machine can: exports, comments and JSDoc, naming,
 * language features, the type system and the licence header. Rules a machine
 * cannot judge are listed for review in the platform repository's
 * docs/standards/CODING_STANDARDS.md (one level above joseph-terminal).
 *
 * Inline exceptions name the rule and give a reason after ` -- `:
 * `// eslint-disable-next-line <rule> -- <reason>`.
 */

import eslintComments from '@eslint-community/eslint-plugin-eslint-comments';
import js from '@eslint/js';
import jsdoc from 'eslint-plugin-jsdoc';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

import {localPlugin} from './scripts/lint/eslint-plugin-local.mjs';

/** Every hand-written source file the linter checks. */
const SOURCE_FILES = ['**/*.{ts,tsx,js,mjs,cjs}'];

/** TypeScript sources (type-aware rules run on these). */
const TS_FILES = ['**/*.{ts,tsx}'];

/** JavaScript scripts and configs (Google JavaScript guide). */
const JS_FILES = ['**/*.{js,mjs,cjs}'];

/** Unit tests (the only place `@ts-expect-error` may appear). */
const TEST_FILES = ['**/*.test.{ts,tsx}', '**/tests/**/*.{ts,tsx}'];

/**
 * TypeScript files outside every tsconfig `include`; the project service
 * type-checks them with tsconfig.base.json.
 */
const DEFAULT_PROJECT_FILES = ['vitest.config.ts'];

/** Declarations that need JSDoc in addition to the plugin defaults. */
const JSDOC_CONTEXTS = [
  'TSInterfaceDeclaration',
  'TSTypeAliasDeclaration',
  'TSEnumDeclaration',
  'TSMethodSignature',
  'Program > VariableDeclaration > VariableDeclarator > ArrowFunctionExpression',
  'ExportNamedDeclaration > VariableDeclaration > VariableDeclarator > ArrowFunctionExpression',
  'ExportNamedDeclaration[declaration.type="VariableDeclaration"]',
];

/** Functions whose `@param`/`@return` tags are checked. */
const JSDOC_FUNCTION_CONTEXTS = [
  'FunctionDeclaration',
  'MethodDefinition',
  'Program > VariableDeclaration > VariableDeclarator > ArrowFunctionExpression',
  'ExportNamedDeclaration > VariableDeclaration > VariableDeclarator > ArrowFunctionExpression',
];

/** Function parameters written as patterns (for destructuring rules). */
const PARAM_PATTERNS = [
  ':function > ObjectPattern',
  ':function > AssignmentPattern > ObjectPattern',
];

/**
 * Builds one `no-restricted-syntax` entry.
 * @param {string} selector esquery selector that matches the forbidden
 *     syntax.
 * @param {string} message Message naming the Google guide section.
 * @return {{selector: string, message: string}} The rule entry.
 */
function restricted(selector, message) {
  return {selector, message};
}

/** Syntax the Google guides forbid and no dedicated rule catches. */
const RESTRICTED_SYNTAX = [
  restricted(
    ':matches(PropertyDefinition, MethodDefinition, AccessorProperty) > PrivateIdentifier.key',
    'Use the TypeScript `private` modifier, not #private (tsguide "No #private fields").'
  ),
  restricted(
    'TSEnumDeclaration[const=true]',
    'Use a plain `enum`, not `const enum` (tsguide "Const enums").'
  ),
  restricted(
    'TSModuleDeclaration[kind="namespace"], TSModuleDeclaration[kind="module"][id.type="Identifier"]',
    'Use modules, not namespaces (tsguide "Use modules not namespaces").'
  ),
  restricted(
    'TSExportAssignment',
    'Use ES module exports, not `export =` (tsguide "Use modules not namespaces").'
  ),
  restricted(
    'ExportNamedDeclaration > VariableDeclaration[kind=/^(let|var)$/]',
    'Exports must be `const`; expose mutable state through a getter function (tsguide "Mutable exports").'
  ),
  restricted(
    ':matches(Program, Program > ExportNamedDeclaration) > VariableDeclaration > VariableDeclarator:not([id.typeAnnotation]) > ArrowFunctionExpression.init',
    'Declare named top-level functions with `function name() {}`; an arrow function is for when a type annotation is required (tsguide "Prefer function declarations for named functions").'
  ),
  restricted(
    'FunctionExpression:not([generator=true]):not(MethodDefinition > FunctionExpression):not(TSAbstractMethodDefinition > FunctionExpression):not(Property[method=true] > FunctionExpression):not(Property[kind="get"] > FunctionExpression):not(Property[kind="set"] > FunctionExpression)',
    'Use an arrow function, not a function expression (tsguide "Do not use function expressions").'
  ),
  restricted(
    'Decorator',
    'Do not define or use decorators outside a framework that requires them (tsguide "Decorators").'
  ),
  restricted(
    'TSTypeAliasDeclaration > TSUnionType.typeAnnotation > :matches(TSNullKeyword, TSUndefinedKeyword)',
    'Type aliases must not include |null or |undefined; add it where the alias is used (tsguide "Nullable/undefined type aliases").'
  ),
  restricted(
    'TSIndexSignature > Identifier[name=/^(k|key)$/]',
    'Give the index signature key a meaningful label, e.g. {[userName: string]: T} (tsguide "Indexable types").'
  ),
  restricted(
    ':matches(MethodDefinition, PropertyDefinition, AccessorProperty)[static=true] ThisExpression, StaticBlock ThisExpression',
    'Do not use `this` in a static context; name the class (tsguide "Avoid static this references").'
  ),
  restricted(
    ':matches(PropertyDefinition, MethodDefinition, AccessorProperty)[computed=true]:not([key.type="MemberExpression"][key.object.name="Symbol"])',
    'Class members may only use computed names for symbols (tsguide "Computed properties").'
  ),
  restricted(
    'AssignmentExpression > MemberExpression.left[object.property.name="prototype"], AssignmentExpression > MemberExpression.left[property.name="prototype"], CallExpression[callee.object.name="Object"][callee.property.name="setPrototypeOf"]',
    'Do not manipulate prototypes directly (tsguide "Do not manipulate prototypes directly").'
  ),
  restricted(
    'CallExpression[callee.object.name="Object"][callee.property.name=/^definePropert(y|ies)$/] Property[key.name=/^(get|set)$/]',
    'Define getters and setters with class accessors, not Object.defineProperty (tsguide "Getters and setters").'
  ),
  restricted(
    ':matches(NewExpression, CallExpression)[callee.name="Array"]',
    'Use an array literal or Array.from, not the Array constructor (tsguide "Do not use the Array constructor").'
  ),
  restricted(
    'CallExpression[callee.name=/^(Aggregate|Eval|Range|Reference|Syntax|Type|URI)?Error$/]',
    'Instantiate errors with `new` (tsguide "Instantiate errors using new").'
  ),
  restricted(
    'CallExpression[callee.name="parseFloat"], CallExpression[callee.object.name="Number"][callee.property.name="parseFloat"]',
    'Use Number() and check the result for NaN, not parseFloat (tsguide "Coercing to numbers").'
  ),
  restricted(
    'CallExpression:matches([callee.name="parseInt"], [callee.object.name="Number"][callee.property.name="parseInt"]):matches([arguments.length<2], [arguments.1.value=10])',
    'Use Number() for decimal strings; parseInt is only for a non-decimal radix (tsguide "Coercing to numbers").'
  ),
  restricted(
    'CallExpression[callee.property.name=/^(add|remove)EventListener$/] > CallExpression.arguments[callee.property.name="bind"]',
    'Do not bind() in the expression that installs an event handler; use an arrow function property (tsguide "Event handlers").'
  ),
  restricted(
    ':function > AssignmentPattern[left.type="ObjectPattern"][right.type="ObjectExpression"][right.properties.length>0], :function > AssignmentPattern[left.type="ArrayPattern"][right.type="ArrayExpression"][right.elements.length>0]',
    'Default a destructured parameter to {} or [] and put the defaults on the left-hand side (tsguide "Object destructuring", "Array destructuring").'
  ),
  restricted(
    PARAM_PATTERNS.map(
      pattern =>
        `${pattern} > Property > :matches(ObjectPattern, ArrayPattern).value, ` +
        `${pattern} > Property > AssignmentPattern > :matches(ObjectPattern, ArrayPattern).left, ` +
        `${pattern} > Property[computed=true]`
    ).join(', '),
    'Parameter destructuring is a single level of unquoted shorthand properties (tsguide "Object destructuring").'
  ),
  restricted(
    'AssignmentExpression > MemberExpression.left[property.name=/^(inner|outer)HTML$/], CallExpression[callee.property.name="insertAdjacentHTML"], CallExpression[callee.object.name="document"][callee.property.name=/^write(ln)?$/], JSXAttribute[name.name="dangerouslySetInnerHTML"]',
    'Do not write HTML strings into the DOM (tsguide "Conformance": security patterns).'
  ),
];

/**
 * Naming rules (tsguide "Naming"): acronyms are words (`loadHttpUrl`), no
 * `_` prefixes or suffixes except the `_` that marks an unused binding, and
 * CONSTANT_CASE only at module level.
 */
const NAMING_CONVENTION = [
  'error',
  {
    selector: 'default',
    format: ['strictCamelCase'],
    leadingUnderscore: 'forbid',
    trailingUnderscore: 'forbid',
  },
  {selector: 'import', format: null},
  {
    selector: 'variable',
    modifiers: ['global'],
    format: ['strictCamelCase', 'UPPER_CASE', 'StrictPascalCase'],
    leadingUnderscore: 'forbid',
    trailingUnderscore: 'forbid',
  },
  {
    selector: 'variable',
    format: ['strictCamelCase', 'StrictPascalCase'],
    leadingUnderscore: 'forbid',
    trailingUnderscore: 'forbid',
  },
  {
    selector: 'variable',
    modifiers: ['unused'],
    format: ['strictCamelCase'],
    leadingUnderscore: 'allow',
    trailingUnderscore: 'forbid',
  },
  {selector: 'variable', modifiers: ['destructured'], format: null},
  {selector: 'function', format: ['strictCamelCase', 'StrictPascalCase']},
  {
    selector: 'parameter',
    format: ['strictCamelCase'],
    leadingUnderscore: 'forbid',
    trailingUnderscore: 'forbid',
  },
  {
    selector: 'parameter',
    modifiers: ['unused'],
    format: ['strictCamelCase'],
    leadingUnderscore: 'allow',
    trailingUnderscore: 'forbid',
  },
  {selector: 'parameter', modifiers: ['destructured'], format: null},
  {selector: 'typeLike', format: ['StrictPascalCase']},
  {selector: 'typeParameter', format: ['PascalCase']},
  {selector: 'enumMember', format: ['UPPER_CASE']},
  {
    selector: 'classProperty',
    modifiers: ['static', 'readonly'],
    format: ['UPPER_CASE', 'strictCamelCase'],
  },
  {
    // Wire names (snake_case contract fields), HTTP headers and other keys
    // that external formats dictate.
    selector: [
      'objectLiteralProperty',
      'typeProperty',
      'objectLiteralMethod',
      'typeMethod',
    ],
    format: null,
  },
  {
    // Platform protocol names (`JSON.stringify` calls `toJSON`).
    selector: 'method',
    filter: {regex: '^toJSON$', match: true},
    format: null,
  },
];

export default tseslint.config(
  {
    ignores: [
      '**/node_modules/**',
      '**/.next/**',
      '**/.next-e2e/**',
      '**/coverage/**',
      '**/playwright-report/**',
      '**/test-results/**',
      '**/generated/**',
      'apps/web/next-env.d.ts',
    ],
  },
  {
    linterOptions: {
      reportUnusedDisableDirectives: 'error',
      reportUnusedInlineConfigs: 'error',
    },
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: SOURCE_FILES,
    plugins: {
      '@eslint-community/eslint-comments': eslintComments,
      jsdoc,
      'local': localPlugin,
    },
    rules: {
      // Inline exceptions name their rule and give a reason after ` -- `.
      '@eslint-community/eslint-comments/disable-enable-pair': [
        'error',
        {allowWholeFile: false},
      ],
      '@eslint-community/eslint-comments/no-aggregating-enable': 'error',
      '@eslint-community/eslint-comments/no-duplicate-disable': 'error',
      '@eslint-community/eslint-comments/no-unlimited-disable': 'error',
      '@eslint-community/eslint-comments/no-unused-enable': 'error',
      '@eslint-community/eslint-comments/require-description': [
        'error',
        {ignore: []},
      ],

      // Project checks (scripts/lint/eslint-plugin-local.mjs).
      'local/license-header': 'error',
      'local/file-structure': 'error',
      'local/comment-style': 'error',
      'local/jsdoc-tag-indent': 'error',
      'local/loop-braces': 'error',
      'local/todo-format': 'error',
      'local/filename-case': 'error',

      // Source file structure and exports.
      'no-restricted-exports': [
        'error',
        {
          restrictDefaultExports: {
            direct: true,
            named: true,
            defaultFrom: true,
            namedFrom: true,
            namespaceFrom: true,
          },
        },
      ],
      'no-duplicate-imports': ['error', {includeExports: false}],
      'no-restricted-syntax': ['error', ...RESTRICTED_SYNTAX],

      // Local variable declarations.
      'no-var': 'error',
      'prefer-const': 'error',
      'one-var': ['error', 'never'],
      'no-use-before-define': 'off',
      '@typescript-eslint/no-use-before-define': [
        'error',
        {
          functions: false,
          classes: false,
          variables: false,
          enums: false,
          typedefs: false,
          ignoreTypeReferences: true,
        },
      ],

      // Literals, coercion and disallowed features.
      'no-object-constructor': 'error',
      'no-new-wrappers': 'error',
      'no-multi-str': 'error',
      'no-octal-escape': 'error',
      'no-implicit-coercion': [
        'error',
        {boolean: false, number: true, string: true},
      ],
      'no-eval': 'error',
      'no-new-func': 'error',
      'no-extend-native': 'error',
      'no-proto': 'error',
      'prefer-rest-params': 'error',
      'prefer-spread': 'error',

      // Control structures.
      'curly': ['error', 'multi-line'],
      'default-case': ['error', {commentPattern: '(?!)'}],
      'default-case-last': 'error',
      'eqeqeq': ['error', 'always', {null: 'ignore'}],
      'guard-for-in': 'error',

      // Classes: methods and constructors are set off by blank lines.
      'lines-between-class-members': [
        'error',
        {
          enforce: [
            {blankLine: 'always', prev: '*', next: 'method'},
            {blankLine: 'always', prev: 'method', next: '*'},
          ],
        },
      ],

      // Naming.
      '@typescript-eslint/naming-convention': NAMING_CONVENTION,
      '@typescript-eslint/no-unused-vars': [
        'error',
        {
          args: 'after-used',
          varsIgnorePattern: '^_',
          caughtErrors: 'all',
          destructuredArrayIgnorePattern: '^_',
          ignoreRestSiblings: true,
        },
      ],

      // JSDoc (tsguide "Comments and documentation").
      'jsdoc/check-alignment': 'error',
      'jsdoc/check-param-names': ['error', {checkDestructured: false}],
      'jsdoc/multiline-blocks': [
        'error',
        {noZeroLineText: true, noFinalLineText: true},
      ],
      'jsdoc/no-bad-blocks': 'error',
      'jsdoc/no-multi-asterisks': 'error',
      'jsdoc/require-asterisk-prefix': 'error',
      'jsdoc/require-description': [
        'error',
        {
          // tsguide "Class comments": constructors may omit the description.
          checkConstructors: false,
          contexts: ['any'],
          exemptedBy: ['fileoverview', 'inheritdoc', 'license', 'type'],
        },
      ],
      'jsdoc/require-file-overview': [
        'error',
        {
          tags: {
            file: {
              initialCommentsOnly: true,
              mustExist: true,
              preventDuplicates: true,
            },
          },
        },
      ],
      'jsdoc/require-hyphen-before-param-description': ['error', 'never'],
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
          // Write real documentation; the fixer would insert empty stubs.
          enableFixer: false,
          checkConstructors: false,
          checkGetters: true,
          checkSetters: true,
        },
      ],
      'jsdoc/require-param': [
        'error',
        {
          checkDestructured: false,
          checkDestructuredRoots: false,
          contexts: JSDOC_FUNCTION_CONTEXTS,
          enableFixer: false,
        },
      ],
      'jsdoc/require-param-description': 'error',
      'jsdoc/require-returns': [
        'error',
        {
          checkGetters: false,
          forceReturnsWithAsync: false,
          contexts: JSDOC_FUNCTION_CONTEXTS,
        },
      ],
      'jsdoc/require-returns-check': 'error',
      'jsdoc/require-returns-description': 'error',
      'jsdoc/require-throws': 'error',
    },
  },
  {
    files: TS_FILES,
    languageOptions: {
      parserOptions: {
        projectService: {
          allowDefaultProject: DEFAULT_PROJECT_FILES,
          defaultProject: 'tsconfig.base.json',
        },
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {'react-hooks': reactHooks},
    settings: {
      jsdoc: {
        mode: 'typescript',
        tagNamePreference: {returns: 'return', file: 'fileoverview'},
      },
    },
    rules: {
      // Types, assertions and the compiler.
      '@typescript-eslint/array-type': ['error', {default: 'array-simple'}],
      '@typescript-eslint/ban-ts-comment': [
        'error',
        {
          'ts-check': false,
          'ts-expect-error': true,
          'ts-ignore': true,
          'ts-nocheck': true,
        },
      ],
      '@typescript-eslint/consistent-type-assertions': [
        'error',
        {assertionStyle: 'as', objectLiteralTypeAssertions: 'never'},
      ],
      '@typescript-eslint/consistent-type-definitions': ['error', 'interface'],
      '@typescript-eslint/consistent-type-exports': [
        'error',
        {fixMixedExportsWithInlineTypeSpecifier: true},
      ],
      '@typescript-eslint/consistent-type-imports': [
        'error',
        {fixStyle: 'inline-type-imports'},
      ],
      '@typescript-eslint/explicit-module-boundary-types': 'error',
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/no-inferrable-types': [
        'error',
        {ignoreParameters: false, ignoreProperties: true},
      ],
      '@typescript-eslint/no-non-null-assertion': 'error',
      '@typescript-eslint/no-unnecessary-type-assertion': 'error',
      '@typescript-eslint/triple-slash-reference': [
        'error',
        {lib: 'never', path: 'never', types: 'never'},
      ],

      // Classes.
      '@typescript-eslint/explicit-member-accessibility': [
        'error',
        {accessibility: 'no-public'},
      ],
      '@typescript-eslint/no-extraneous-class': 'error',
      'no-useless-constructor': 'off',
      '@typescript-eslint/no-useless-constructor': 'error',
      '@typescript-eslint/parameter-properties': [
        'error',
        {prefer: 'parameter-property'},
      ],
      '@typescript-eslint/prefer-readonly': 'error',
      '@typescript-eslint/dot-notation': [
        'error',
        {
          allowIndexSignaturePropertyAccess: true,
          allowPrivateClassPropertyAccess: false,
          allowProtectedClassPropertyAccess: false,
        },
      ],

      // Functions.
      '@typescript-eslint/no-confusing-void-expression': [
        'error',
        {ignoreArrowShorthand: false, ignoreVoidOperator: true},
      ],

      // Exceptions and other runtime checks that need type information.
      '@typescript-eslint/only-throw-error': 'error',
      '@typescript-eslint/prefer-promise-reject-errors': 'error',
      '@typescript-eslint/no-for-in-array': 'error',
      // tsguide "Using spread syntax" is about objects with prototypes;
      // spreading a string into code points is fine.
      '@typescript-eslint/no-misused-spread': ['error', {allow: ['string']}],
      'no-implied-eval': 'off',
      '@typescript-eslint/no-implied-eval': 'error',
      '@typescript-eslint/restrict-plus-operands': [
        'error',
        {
          allowAny: false,
          allowBoolean: false,
          allowNullish: false,
          allowNumberAndString: false,
          allowRegExp: false,
        },
      ],

      // JSDoc carries no types in TypeScript; the keywords say it.
      'jsdoc/check-tag-names': ['error', {typed: true}],
      'jsdoc/no-defaults': 'error',
      'jsdoc/no-types': 'error',

      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
    },
  },
  {
    files: TEST_FILES,
    rules: {
      // tsguide "@ts-ignore": unit tests may use @ts-expect-error, with a
      // reason, when testing unchecked runtime values.
      '@typescript-eslint/ban-ts-comment': [
        'error',
        {
          'ts-check': false,
          'ts-expect-error': 'allow-with-description',
          'ts-ignore': true,
          'ts-nocheck': true,
        },
      ],
    },
  },
  {
    files: JS_FILES,
    languageOptions: {
      globals: {
        console: 'readonly',
        process: 'readonly',
        URL: 'readonly',
      },
    },
    settings: {
      jsdoc: {
        mode: 'typescript',
        tagNamePreference: {returns: 'return', file: 'fileoverview'},
      },
    },
    rules: {
      // jsguide 7.8/7.10: JavaScript JSDoc declares parameter and return
      // types in braces.
      'jsdoc/check-tag-names': 'error',
      'jsdoc/require-param-type': 'error',
      'jsdoc/require-returns-type': 'error',
      'jsdoc/valid-types': 'error',
      'no-implied-eval': 'error',
      'no-throw-literal': 'error',
      'prefer-promise-reject-errors': 'error',
    },
  },
  {
    files: ['**/*.cjs'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: {module: 'writable', require: 'readonly'},
    },
    rules: {'@typescript-eslint/no-require-imports': 'off'},
  },
  {
    // Framework entry points and tool configs must default-export.
    files: [
      'apps/web/src/app/**/{page,layout,not-found,error,global-error,loading,template,default}.tsx',
      '**/*.config.{ts,mjs,js,cjs}',
    ],
    rules: {'no-restricted-exports': 'off'},
  }
);
