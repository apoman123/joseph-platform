# Coding standards

Joseph follows the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) and the [Google TypeScript Style Guide](https://google.github.io/styleguide/tsguide.html). "Enforced by" names the check that fails CI.

## Python (both engines)

| Rule | Enforced by |
|---|---|
| 80 columns, 4-space indent, double quotes | `ruff format --check` |
| Google docstrings on **every** module, class and function, public or private (`Args:`, `Returns:`/`Yields:`, `Raises:`; classes list `Attributes:`; `__init__` args may go in the class docstring) | ruff `D` (google) + `tests/architecture/test_code_standards.py` |
| Import modules, not names (`from joseph.domain import market`, then `market.Candle`); exempt: `typing`, `collections.abc`, `typing_extensions`, `__future__`. No relative imports; one import per line | `test_code_standards.py`, ruff isort `force-single-line` |
| Full annotations, `mypy --strict` clean | `mypy` |
| Naming: `module_name`, `ClassName`, `function_name`, `CONSTANT_NAME`; exceptions end in `Error` | ruff `N` |
| Money, prices and quantities are `decimal.Decimal`, decimal strings on the wire; never `float` on an order path | contract tests, review |
| Time is UTC epoch milliseconds in `int` fields ending `_ms`; bars are `[start_ms, end_ms)` | domain validation |
| Domain imports only the stdlib; adapters wire only in `bootstrap.py`; plugins never import each other | import-linter (`tests/architecture/test_layering.py`) |
| `logging` with lazy `%` formatting; secrets redacted, never logged | security tests |
| pytest; every test has a one-line docstring | `test_code_standards.py` |

## TypeScript (joseph-terminal)

| Rule | Enforced by |
|---|---|
| Named exports only (framework entry files such as `page.tsx` excepted) | ESLint `no-restricted-syntax` |
| JSDoc with a description on every class, interface, type alias, enum, function, method and module-level arrow function; `@param name description`, `@return description`; no types in JSDoc; files start with `@fileoverview` | `eslint-plugin-jsdoc` |
| No `any` (use `unknown`), no non-null `!`, no `namespace`, `const enum` or `#private` | ESLint |
| `interface` for object shapes, `type` for unions; `import type` for type-only imports | ESLint |
| `UpperCamelCase` types and components, `lowerCamelCase` functions and variables, `CONSTANT_CASE` module constants, kebab-case files; contract fields keep wire `snake_case` | ESLint `naming-convention` |
| Prettier (gts): single quotes, no bracket spacing, ES5 trailing commas, 80 columns | `prettier --check` |
| Contract types are generated from JSON Schema, never hand-written | `pnpm contracts:check` |
| Order-path prices and quantities use `Decimal` from `@joseph/contracts`; `number` only for display and chart coordinates | review, unit tests |
| Package boundaries ([architecture](../architecture/INDEX.md#dependency-rules-enforced-in-ci)) | `dependency-cruiser` (`pnpm arch:check`) |

## Contracts

`joseph-terminal/packages/contracts/schemas/*.schema.json` (JSON Schema 2020-12) is the single source of truth. TypeScript types are generated; Python models are contract-tested against the same files; `packages/contracts/fixtures/*.fixture.json` holds valid and invalid examples both stacks must agree on.
