# Coding standards

The rules every contribution follows: the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html), the [Google TypeScript Style Guide](https://google.github.io/styleguide/tsguide.html), the [JavaScript guide](https://google.github.io/styleguide/jsguide.html) for `.mjs`/`.cjs` scripts and the [HTML/CSS guide](https://google.github.io/styleguide/htmlcssguide.html) for stylesheets, plus the project rules below. "Enforced by" names the check that fails CI; run `make lint typecheck` before opening a pull request.

## Python (both engines and the system tests)

`GPYnnn` codes come from `tests/architecture/test_code_standards.py` (system tests: `tests/test_code_standards.py`); "pyguide" sections are the Google guide's. Tests (`test*` functions) may keep a one-line docstring; fixtures, fakes and helpers follow every rule.

| Rule | Enforced by |
|---|---|
| Licence header first (after any shebang): `# Copyright <year> apoman123`, `# SPDX-License-Identifier: MIT`, blank line (pyguide 3.8.2) | GPY010 |
| 80 columns, 4-space indent, double quotes, no backslash continuation (3.2–3.6) | `ruff format --check`, ruff `E`/`W`, GPY040 |
| A docstring on **every** module, class and function, public or private; one-line summary ending in a period, one mood per file (3.8) | ruff `D` (google), GPY011, GPY017 |
| `Args:` names every parameter (no types), `Returns:`/`Yields:` when something comes back (a one-liner starting "Returns ..." may stand in), `Raises:` lists what the function raises; `__init__` args in the `__init__` docstring (3.8.3) | pydoclint, GPY012, GPY013 |
| Classes list public attributes under `Attributes:`; properties read like attributes; summaries say what the instance or error is (3.8.3, 3.8.4) | GPY014–GPY016, GPY018 |
| Comments: `# ` then text; explain why; no commented-out code; `# TODO(link): what` (3.8.5, 3.12) | GPY019, ruff `ERA`, `TD` |
| Import modules, not names (`from joseph.domain import market`, then `market.Candle`); exempt: `typing`, `collections.abc`, `typing_extensions`, `__future__`. No relative imports; aliases only standard ones (`sa`, `pa`); one import per line, at the top (2.2, 3.13) | GPY020–GPY022, ruff `I` (`force-single-line`), `E402`, `PLC0415` |
| Exceptions: no bare or catch-all `except` outside isolation points; no `assert` for logic outside tests (2.4) | ruff `E722`, `BLE`, `S101` |
| No `nonlocal` or `global` state; nest functions only to close over a local (2.5, 2.6) | GPY030, GPY031, ruff `PLW0602`/`PLW0603` |
| Comprehensions: one `for`, one `if`; conditional expressions one line per part (2.7, 2.11) | GPY032, GPY033 |
| No `@staticmethod`; `@classmethod` only for named constructors; no trivial get/set properties; no power features (2.13, 2.17, 2.19) | GPY034–GPY037 |
| Unused arguments removed with `del name  # Unused.` (2.1) | ruff `ARG` (not in tests) |
| Strings: f-string, `%` or `format`, not `+` chains or `+=` in loops; logging takes a `%` pattern literal (3.10) | GPY042, GPY043, ruff `G` |
| Scripts run `main()` behind `if __name__ == "__main__":`; no work at import (3.17) | GPY046, GPY047 |
| Full annotations, `mypy --strict` clean, tests included (each engine's `src`, `tests` and `scripts`; system-tests' `tests` and `launchers`); aliases `X: TypeAlias = ...` in CapWords; no type comments; descriptive TypeVars (2.21, 3.19) | `mypy`, GPY048–GPY050 |
| Naming: `module_name`, `ClassName`, `function_name`, `CONSTANT_NAME`; exceptions end in `Error`; no `__private` names (3.16) | ruff `N`, GPY044, GPY045 |
| Inline exceptions name the code and give a reason: `# noqa: BLE001 - <why>`, `# type: ignore[code]  # <why>` (2.1) | GPY001, GPY002, ruff `PGH`, `RUF100` |
| Money, prices and quantities are `decimal.Decimal`, decimal strings on the wire; never `float` on an order path | contract tests, review |
| Time is UTC epoch milliseconds in `int` fields ending `_ms`; bars are `[start_ms, end_ms)` | domain validation |
| Domain imports only the stdlib; adapters wire only in `bootstrap.py`; plugins never import each other | import-linter (`tests/architecture/test_layering.py`) |
| Secrets redacted, never logged | security tests |

## TypeScript and JavaScript (joseph-terminal)

`.ts`/`.tsx` follow the TypeScript guide; `.mjs`/`.cjs` scripts and tool configs follow the JavaScript guide with the same project choices. Generated code (`packages/contracts/src/generated/`, `apps/web/next-env.d.ts`) is exempt.

| Rule | Enforced by |
|---|---|
| Licence header first in every file: `/**` ` * @license` ` * Copyright 2026 Joseph Terminal contributors` ` * SPDX-License-Identifier: MIT` ` */`, then a blank line | ESLint `local/license-header` |
| File order: licence, `@fileoverview` JSDoc, `'use client'` (Next.js only), imports, implementation, one blank line between sections; every file has an `@fileoverview` | `local/file-structure`, `jsdoc/require-file-overview` |
| Prettier (gts): single quotes, no bracket spacing, ES5 trailing commas, 80 columns, consistent quoting of object keys | `prettier --check` (`.ts`, `.tsx`, `.mjs`, `.cjs`, `.css`, `package.json`, `tsconfig*.json`) |
| Named exports only (framework entry files such as `page.tsx` and `*.config.*` excepted); no `export let`; no static-only container classes; no `namespace`, `require` (outside `.cjs`) or `/// <reference>` | ESLint `no-restricted-exports`, `no-restricted-syntax`, `no-extraneous-class`, `no-namespace`, `no-require-imports`, `triple-slash-reference` |
| `import type` for type-only imports, `export type` for type re-exports, one import statement per module | ESLint `consistent-type-imports`, `consistent-type-exports`, `no-duplicate-imports` |
| JSDoc on every class, method, function (nested too), top-level arrow function, interface, type alias, enum and exported constant; a description (constructors excepted); `@param name description` for each parameter, `@return description`, `@throws` when the function throws; no types in TS JSDoc, `{Type}` required in JS JSDoc; no tags the TS keywords express (`@private`, `@override`, ...); wrapped tag lines indented 4 spaces | `eslint-plugin-jsdoc`, `local/jsdoc-tag-indent` |
| `//` for implementation comments, multi-line ones as consecutive `//` lines; `/** */` only before the declaration or member it documents; `/* */` only inline (`/* name= */`) and for JSX children | `local/comment-style` |
| TODOs as `TODO: <link or owner> - <what>`; no `FIXME`/`XXX` | `local/todo-format` |
| Inline exceptions name the rule and give a reason: `// eslint-disable-next-line <rule> -- <reason>` (`/* stylelint-disable-next-line <rule> -- <reason> */` in CSS); unused exceptions fail | `eslint-comments/require-description`, `no-unlimited-disable`, `reportUnusedDisableDirectives`; stylelint `reportDescriptionlessDisables`, `reportNeedlessDisables` |
| `const`/`let` only, one variable per declaration, no use before declaration | ESLint `no-var`, `prefer-const`, `one-var`, `no-use-before-define` |
| Named top-level functions are `function` declarations (arrows only when a type annotation is needed); no function expressions; a concise arrow body only when its value is used (`() => void f()` or a block otherwise) | `no-restricted-syntax`, `no-confusing-void-expression` |
| Braces on every loop and on every `if` that does not fit on one line | ESLint `curly`, `local/loop-braces` |
| Every `switch` has a last `default` group; no fall-through | ESLint `default-case`, `default-case-last`, `no-fallthrough`, `tsc` |
| `===`/`!==` only (`== null` allowed) | ESLint `eqeqeq` |
| Throw and reject only `Error` objects, created with `new`; empty `catch` blocks carry a comment | ESLint `only-throw-error`, `prefer-promise-reject-errors`, `no-throw-literal`, `no-restricted-syntax`, `no-empty` |
| `for...in` only on dict objects, with an own-property filter, never on arrays | ESLint `guard-for-in`, `no-for-in-array` |
| No `Array()`/`Object()` constructors, wrapper objects, unary `+`/`'' +` coercion, `parseFloat`, decimal `parseInt`, `eval`, `new Function`, prototype changes, HTML strings into the DOM, decorators | ESLint core rules, `no-restricted-syntax` |
| Classes: no `public` (except non-readonly parameter properties), no `#private`, `readonly` for private members never reassigned, parameter properties instead of plumbing, no useless constructors, no `this` in static code, methods set off by blank lines | ESLint `explicit-member-accessibility`, `no-restricted-syntax`, `prefer-readonly`, `parameter-properties`, `no-useless-constructor`, `lines-between-class-members` |
| Types: no `any` (use `unknown`), no `{}`, no wrapper types; `interface` for object shapes; `T[]` for simple element types and `Array<T>` otherwise; type aliases never include `\| null`/`\| undefined`; no annotations on literal-initialised variables | ESLint `no-explicit-any`, `no-empty-object-type`, `no-wrapper-object-types`, `consistent-type-definitions`, `array-type`, `no-restricted-syntax`, `no-inferrable-types` |
| Assertions: `as` syntax, never on object literals (annotate instead), none that are unnecessary, no non-null `!`; each remaining `as` is obvious or has a comment saying why it is safe | ESLint `consistent-type-assertions`, `no-unnecessary-type-assertion`, `no-non-null-assertion`; review |
| No `@ts-ignore`, `@ts-nocheck` or `@ts-expect-error` (unit tests may use `@ts-expect-error: <reason>`) | ESLint `ban-ts-comment` |
| Naming: `UpperCamelCase` types and components, `lowerCamelCase` functions, variables and members, `CONSTANT_CASE` module constants, enum members and `static readonly` constants (never locals); acronyms as words (`loadHttpUrl`, `customerId`); no `_` prefix or suffix except `_` on an unused required binding; trailing unused parameters omitted; kebab-case files; contract fields keep wire `snake_case` | ESLint `naming-convention` (strict camel case), `no-unused-vars`, `local/filename-case` |
| Third-person JSDoc summaries ("Returns ..."), Markdown lists in JSDoc, minimal exported surface, descriptive names, focused `try` blocks, no named callbacks with optional parameters | review |
| Contract types are generated from JSON Schema, never hand-written | `pnpm contracts:check` |
| Order-path prices and quantities use `Decimal` from `@joseph/contracts`; `number` only for display and chart coordinates | review, unit tests |
| Package boundaries ([architecture](../architecture/INDEX.md#dependency-rules-enforced-in-ci)) | `dependency-cruiser` (`pnpm arch:check`) |

## CSS (joseph-terminal)

| Rule | Enforced by |
|---|---|
| Licence header first: `/*` ` * Copyright 2026 Joseph Terminal contributors` ` * SPDX-License-Identifier: MIT` ` */`, then a blank line | stylelint `local/license-header` |
| 2-space indent, lowercase, one selector and one declaration per line, `prop: value;`, single quotes, leading zeros (`0.5em`) | `prettier --check` |
| Valid CSS only; no duplicate or ineffective declarations; a shorthand never overrides a longhand declared before it | stylelint `*-no-unknown`, `*-no-invalid`, `declaration-block-no-duplicate-properties`, `declaration-block-no-shorthand-property-overrides` |
| Declarations in alphabetical order (vendor prefixes ignored for sorting) | `stylelint-order` `properties-alphabetical-order` |
| A blank line between rules | stylelint `rule-empty-line-before`, `at-rule-empty-line-before` |
| Global class names: lowercase words joined by `-`, BEM `__element`/`--modifier` allowed; CSS Module class names `lowerCamelCase` (read as `styles.name`); kebab-case custom properties and keyframes | stylelint `selector-class-pattern`, `custom-property-pattern`, `keyframes-name-pattern` |
| No ID selectors, no type-qualified classes (`div.error`), no `!important` (inline exception with a reason if unavoidable) | stylelint `selector-max-id`, `selector-no-qualifying-type`, `declaration-no-important` |
| Shorthand where it sets every sub-property; no redundant shorthand values; no unit after `0` (custom properties excepted); 3-digit hex; no quotes in `url()`; HTTPS URLs; no `@charset` | stylelint `declaration-block-no-redundant-longhand-properties`, `shorthand-property-no-redundant-values`, `length-zero-no-unit`, `color-hex-length`, `function-url-quotes`, `function-url-scheme-allowed-list`, `at-rule-disallowed-list` |
| Meaningful, short class names; no browser hacks; comments explain why | review |

## Contracts

`joseph-terminal/packages/contracts/schemas/*.schema.json` (JSON Schema 2020-12) is the single source of truth. TypeScript types are generated; Python models are contract-tested against the same files; `packages/contracts/fixtures/*.fixture.json` holds valid and invalid examples both stacks must agree on.
