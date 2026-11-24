# Coding standards

The rules every contribution follows: the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) and the [Google TypeScript Style Guide](https://google.github.io/styleguide/tsguide.html), plus the project rules below. "Enforced by" names the check that fails CI; run `make lint typecheck` before opening a pull request.

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
| Full annotations, `mypy --strict` clean; aliases `X: TypeAlias = ...` in CapWords; no type comments; descriptive TypeVars (2.21, 3.19) | `mypy`, GPY048–GPY050 |
| Naming: `module_name`, `ClassName`, `function_name`, `CONSTANT_NAME`; exceptions end in `Error`; no `__private` names (3.16) | ruff `N`, GPY044, GPY045 |
| Inline exceptions name the code and give a reason: `# noqa: BLE001 - <why>`, `# type: ignore[code]  # <why>` (2.1) | GPY001, GPY002, ruff `PGH`, `RUF100` |
| Money, prices and quantities are `decimal.Decimal`, decimal strings on the wire; never `float` on an order path | contract tests, review |
| Time is UTC epoch milliseconds in `int` fields ending `_ms`; bars are `[start_ms, end_ms)` | domain validation |
| Domain imports only the stdlib; adapters wire only in `bootstrap.py`; plugins never import each other | import-linter (`tests/architecture/test_layering.py`) |
| Secrets redacted, never logged | security tests |

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
