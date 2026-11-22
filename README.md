# Joseph trading platform (V5)

Multi-venue perpetual-futures research and trading: a public charting terminal, a private Python market engine (venues, screening, 7 SMC indicators, rule AST strategies, signals, alerts, backtests) and a private execution engine (risk gateway, OMS, paper/shadow trading, audit, kill switch).

> **Status:** paper and shadow trading work end to end. **Live order placement is disabled and fails closed**: venue signing is not implemented or sandbox-verified. Indicators are line-by-line Python ports of the Pine scripts, kept in the private `apoman123/PineScripts` repository (`python_indicators/`) and installed as a pinned dependency (`parity_status: SPEC_ONLY` until TradingView exports are compared). See `docs/SPEC_GAP_MATRIX.md`.

## Repository layout

| Path | Visibility | What it is |
|---|---|---|
| `joseph-terminal/` | public (MIT) | Next.js terminal + framework-free packages + JSON Schema contracts |
| `joseph-market-engine/` | private | FastAPI service on :8000 |
| `joseph-execution-engine/` | private, separate trust boundary | FastAPI service on :8100 |
| `system-tests/` | private | Cross-service tests that boot both engines |
| `docs/` | private | Manual, runbook, ADRs, reports |

## Quick start

```bash
make setup   # uv + pnpm, locked versions (Python 3.12+, Node 20+)
make dev     # market :8000 · execution :8100 · terminal http://localhost:3000
```

The terminal alone runs in labelled DEMO mode: `cd joseph-terminal && pnpm install && pnpm dev`.
Containers: `cp deploy/.env.example deploy/.env` (fill the secrets) then `make compose-up`.

Every push runs the GitHub Actions pipeline in `.github/workflows/ci-cd.yml` (all test tiers); pushes to `main` and `v*` tags then publish images to GHCR and deploy through protected environments — see [CI/CD](docs/CI_CD.md).

## Tests

| Command | Covers |
|---|---|
| `make test-python` | unit, integration, contract, architecture (Google style + layering), property, replay, security, chaos, in-process system tests — both engines |
| `make test-terminal` | Vitest unit/component/contract/architecture, contracts drift, dependency boundaries, secret and license scans |
| `make test-system` | Real engine processes over HTTP/WebSocket: pipeline → signal → paper order, idempotency, kill switch, tenant isolation, LIVE fail-closed, latency smoke |
| `make test-e2e` | Browser system tests in DEMO mode, axe accessibility, security headers |
| `cd joseph-terminal && pnpm test:e2e:fullstack` | Browser → production build → both real engines |
| `make test-network` | Opt-in checks against the live public venue APIs |
| `make ci` | Everything above except the network tests, plus lint, types and an OpenAPI drift check |

Results of the latest full run: `docs/reports/TEST_REPORT.md`.

## Documentation

- [User manual](docs/USER_MANUAL.md) · [Operations runbook](docs/OPERATIONS.md) · [CI/CD and deployment](docs/CI_CD.md) · [Extending Joseph](docs/EXTENDING.md)
- [Architecture index](docs/architecture/INDEX.md) · [ADRs](docs/adr/) · [Proposal revisions](docs/PROPOSAL_REVISIONS.md) · [Spec gap matrix](docs/SPEC_GAP_MATRIX.md)
- [Coding standards](docs/standards/CODING_STANDARDS.md) (Google Python and TypeScript style, enforced in CI)
- Reports: [threat model](docs/reports/THREAT_MODEL.md) · [licensing](docs/reports/LICENSING_REPORT.md) · [Pine source record](docs/reports/PINE_SOURCE_REVISION.md) · [test report](docs/reports/TEST_REPORT.md)
- API: `joseph-terminal/packages/contracts/{schemas,openapi,asyncapi}`
