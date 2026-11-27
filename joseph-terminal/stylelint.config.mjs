/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview stylelint config implementing the Google HTML/CSS Style
 * Guide's CSS rules for the terminal's stylesheets.
 *
 * Prettier already prints the guide's formatting (indentation, one selector
 * and one declaration per line, `prop: value;`, single quotes, lowercase hex
 * and leading zeros); this config checks the rest. Inline exceptions name the
 * rule and give a reason after ` -- `:
 * `/* stylelint-disable-next-line <rule> -- <reason> *\/`.
 */

import {licenseHeaderPlugin} from './scripts/lint/stylelint-plugin-local.mjs';

/**
 * Lowercase words joined by single hyphens (htmlcssguide "Class Name
 * Delimiters").
 */
const KEBAB = '[a-z][a-z0-9]*(?:-[a-z0-9]+)*';

/**
 * Global class names: kebab-case words, with BEM's `__element` and
 * `--modifier` separators between kebab-case parts.
 */
const GLOBAL_CLASS = `${KEBAB}(?:__[a-z0-9]+(?:-[a-z0-9]+)*)?(?:--[a-z0-9]+(?:-[a-z0-9]+)*)?`;

/**
 * CSS Module class names are read from TypeScript as `styles.name`, so they
 * are lowerCamelCase identifiers; `:global(...)` classes keep the global form.
 */
const MODULE_CLASS = `(?:[a-z][a-zA-Z0-9]*|${GLOBAL_CLASS})`;

/** The stylelint configuration. */
const config = {
  plugins: ['stylelint-order', licenseHeaderPlugin],
  ignoreFiles: [
    'third_party/**',
    '**/node_modules/**',
    '**/.next/**',
    '**/.next-e2e/**',
    '**/coverage/**',
    '**/playwright-report/**',
    '**/test-results/**',
  ],
  reportDescriptionlessDisables: true,
  reportInvalidScopeDisables: true,
  reportNeedlessDisables: true,
  rules: {
    // Licence header (project decision; see scripts/lint/license-header.mjs).
    'local/license-header': true,

    // CSS Validity: use valid CSS; flag code that has no effect.
    'annotation-no-unknown': true,
    'at-rule-descriptor-no-unknown': true,
    'at-rule-descriptor-value-no-unknown': true,
    'at-rule-no-unknown': true,
    'at-rule-prelude-no-invalid': true,
    'block-no-empty': true,
    'color-no-invalid-hex': true,
    'comment-no-empty': true,
    'custom-property-no-missing-var-function': true,
    'declaration-block-no-duplicate-custom-properties': true,
    'declaration-block-no-duplicate-properties': [
      true,
      {ignore: ['consecutive-duplicates-with-different-syntaxes']},
    ],
    // Alphabetical order must never let a shorthand override a longhand
    // that was meant to win; this catches the cases where it would.
    'declaration-block-no-shorthand-property-overrides': true,
    'declaration-property-value-no-unknown': true,
    'font-family-no-duplicate-names': true,
    'function-calc-no-unspaced-operator': true,
    'keyframe-block-no-duplicate-selectors': true,
    'keyframe-declaration-no-important': true,
    'media-feature-name-no-unknown': true,
    'media-feature-name-value-no-unknown': true,
    'media-query-no-invalid': true,
    'named-grid-areas-no-invalid': true,
    'no-duplicate-at-import-rules': true,
    'no-invalid-double-slash-comments': true,
    'no-invalid-position-at-import-rule': true,
    'no-invalid-position-declaration': true,
    'no-irregular-whitespace': true,
    'property-no-unknown': [true, {ignoreProperties: ['composes']}],
    'selector-anb-no-unmatchable': true,
    'selector-pseudo-class-no-unknown': [
      true,
      {ignorePseudoClasses: ['global', 'local']},
    ],
    'selector-pseudo-element-no-unknown': true,
    'selector-type-no-unknown': true,
    'string-no-newline': true,
    'syntax-string-no-invalid': true,
    'unit-no-unknown': true,

    // Capitalization: only lowercase (Prettier lowercases the rest).
    'function-name-case': 'lower',
    'selector-type-case': 'lower',
    'value-keyword-case': 'lower',

    // Class Name Delimiters, ID Selectors, Type Selectors.
    'selector-class-pattern': [
      `^${GLOBAL_CLASS}$`,
      {
        message: name =>
          `Class "${name}" must be lowercase words joined by hyphens ` +
          '(BEM __element/--modifier allowed) (htmlcssguide "Class Name Delimiters").',
      },
    ],
    'custom-property-pattern': [
      `^${KEBAB}$`,
      {message: name => `Custom property "${name}" must be kebab-case.`},
    ],
    'keyframes-name-pattern': [
      `^${KEBAB}$`,
      {message: name => `Keyframes "${name}" must be kebab-case.`},
    ],
    'container-name-pattern': `^${KEBAB}$`,
    'layer-name-pattern': `^${KEBAB}$`,
    'selector-max-id': 0,
    'selector-no-qualifying-type': [true, {ignore: ['attribute']}],

    // Shorthand Properties, 0 and Units, Hexadecimal Notation, Important
    // Declarations, CSS Quotation Marks (url() values).
    'declaration-block-no-redundant-longhand-properties': true,
    'shorthand-property-no-redundant-values': true,
    // Custom properties keep their units: `calc()` cannot use a unitless 0.
    'length-zero-no-unit': [true, {ignore: ['custom-properties']}],
    'color-hex-length': 'short',
    'declaration-no-important': true,
    'function-url-quotes': 'never',

    // Protocol: embedded resources use HTTPS. Encoding: no @charset.
    'function-url-no-scheme-relative': true,
    'function-url-scheme-allowed-list': ['data', 'https'],
    'at-rule-disallowed-list': ['charset'],

    // Declaration Order: alphabetical, vendor prefixes ignored for sorting.
    'order/properties-alphabetical-order': true,

    // Rule Separation: a blank line between rules.
    'rule-empty-line-before': [
      'always',
      {ignore: ['after-comment', 'first-nested']},
    ],
    'at-rule-empty-line-before': [
      'always',
      {
        except: ['blockless-after-same-name-blockless'],
        ignore: ['after-comment', 'first-nested'],
      },
    ],
  },
  overrides: [
    {
      files: ['**/*.module.css'],
      rules: {
        'selector-class-pattern': [
          `^${MODULE_CLASS}$`,
          {
            message: name =>
              `CSS Module class "${name}" must be lowerCamelCase (it is a ` +
              'TypeScript property name), or kebab-case inside :global().',
          },
        ],
      },
    },
  ],
};

export default config;
