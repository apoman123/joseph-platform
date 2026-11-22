# Coding standards

Joseph follows the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) and the [Google TypeScript Style Guide](https://google.github.io/styleguide/tsguide.html). Rules below are enforced by tooling; "enforced by" names the check that fails CI.

## Python (market engine, execution engine)

| Rule | Enforced by |
|---|---|
| 80-column lines, 4-space indent, double quotes (`ruff format`) | `ruff format --check` |
| Google docstrings on **every** module, class and function, public or private (`Args:`, `Returns:`/`Yields:`, `Raises:`; classes list `Attributes:`; `__init__` args may live in the class docstring) | `ruff` `D` rules (google convention) + `tests/architecture/test_code_standards.py` |
| Import modules, not names: `from joseph.domain import market` then `market.Candle`. Exempt: `typing`, `collections.abc`, `typing_extensions`, `__future__`. No relative imports. One import per line. | `test_code_standards.py`, ruff isort `force-single-line` |
| Full type annotations; `mypy --strict` clean | `mypy` |
| Naming: `module_name`, `ClassName`, `function_name`, `CONSTANT_NAME`, exceptions end in `Error` | ruff `N` |
| Money, prices, quantities are `decimal.Decimal`; on the wire they are decimal strings. Never `float` for anything that can reach an order. | contract tests, review |
| Time is UTC epoch milliseconds in `int` fields suffixed `_ms`; bars are `[start_ms, end_ms)` | domain validation |
| Layering: domain imports only the stdlib; adapters wire only in `bootstrap.py`; indicator plugins never import each other | `import-linter` (`tests/architecture/test_layering.py`) |
| `logging` with lazy `%` formatting; secrets are redacted and never logged | security tests |
| Tests: pytest; every test has a one-line docstring describing the behavior | `test_code_standards.py` |

## TypeScript (joseph-terminal)

| Rule | Enforced by |
|---|---|
| Named exports only (framework entry files such as Next.js `page.tsx` are the documented exception) | ESLint `no-restricted-syntax` |
| JSDoc `/** */` with a description on every class, interface, type alias, enum, function, method and module-level arrow function; `@param name description`, `@return description`; no types inside JSDoc; files start with `@fileoverview` | `eslint-plugin-jsdoc` |
| No `any` (use `unknown` and narrow), no non-null `!`, no `namespace`, no `const enum`, no `#private` (use `private`) | ESLint |
| `interface` for object shapes, `type` for unions; `import type` for type-only imports | ESLint |
| Naming: `UpperCamelCase` types/classes/components, `lowerCamelCase` functions and variables, `CONSTANT_CASE` module constants, lower-kebab-case file names; contract fields keep their wire `snake_case` | ESLint `naming-convention` |
| Prettier (gts profile): single quotes, no bracket spacing, ES5 trailing commas, 80 columns | `prettier --check` |
| Contract types are generated from JSON Schema, never hand-written | `pnpm contracts:check` |
| Prices/quantities in order paths use `Decimal` from `@joseph/contracts`; `number` only for display and chart coordinates | review, unit tests |
| Package boundaries: only `chart-core/src/adapters/lwc` imports `lightweight-charts`; packages never import `apps/*`; drawing tools never import each other; no Node built-ins or private-engine paths in browser code | `dependency-cruiser` (`pnpm arch:check`) |

## Contracts

`joseph-terminal/packages/contracts/schemas/*.schema.json` (JSON Schema 2020-12) is the single source of truth. TypeScript types are generated; Python models are contract-tested against the same files, and `packages/contracts/fixtures/*.fixture.json` holds canonical valid/invalid examples that both stacks must agree on.
