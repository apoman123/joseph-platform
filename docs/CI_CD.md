# CI/CD

How the GitHub Actions pipeline of the public repository tests, publishes and deploys Joseph, and how to set it up in your own repository.

## Workflows

| Workflow | Trigger | What it does |
|---|---|---|
| `ci-cd.yml` | every push, `v*` tags, pull requests, manual | All test tiers; then, on `main` and `v*` tags, images and deploys |
| `security.yml` | push to `main`, pull requests, weekly, manual | CodeQL, gitleaks (full history), dependency review on pull requests (blocks high severity and GPL; AGPL is accepted for the integrated application), npm and Python audits |
| `nightly.yml` | daily, manual | Live public-venue network tests and connectivity probe |
| `redeploy.yml` | manual | Deploy a published image tag (rollback) |

A newer `ci-cd` run cancels an older one on the same branch or pull request, except on `main` and tags.

### Jobs of `ci-cd.yml`

| Job | Does |
|---|---|
| `terminal` | Contract generation check, format, lint (ESLint and stylelint), types, Vitest with coverage, boundaries, secret scan, licences, `pnpm audit`, build |
| `terminal-e2e` | DEMO browser tests and accessibility |
| `engines` (×2) | ruff format, ruff, pydoclint and the Google-style checks ([standards](standards/CODING_STANDARDS.md)), `mypy --strict`, pytest with coverage, OpenAPI drift, licences, `pip-audit` |
| `system` | Python style checks and `mypy --strict` of `system-tests`, then both engines as real processes over HTTP and WebSocket |
| `fullstack` | Browser tests against the terminal and both engines |
| `images` (×3) | After all test jobs, for pushes to `main` and `v*` tags |
| `deploy-staging` | `main` → `staging` |
| `release` | `v*` tags: GitHub release with notes and the API contracts bundle |
| `deploy-production` | `v*` tags → `production` |

## Images

`ghcr.io/<owner>/joseph-{market-engine,execution-engine,terminal}`, tagged `sha-<commit>`, `latest` (main) and `vX.Y.Z` / `X.Y.Z` / `X.Y` (tags); non-root with health checks, SBOM and provenance (plus artifact attestations on a public repository). The terminal image bakes `NEXT_PUBLIC_*` from the repository variables `PUBLIC_MARKET_API_URL`, `PUBLIC_MARKET_WS_URL`, `PUBLIC_EXECUTION_API_URL`, `PUBLIC_EXECUTION_WS_URL`; unset means DEMO only. The market image has the built-in indicators plus any [plugin packages](#indicator-plugins-in-the-market-image) in `MARKET_PLUGIN_REQUIREMENTS`. Locally: `make images`.

**Privacy guard:** engine packages must stay private: the market-engine image may contain indicator plugin packages under their own licences, and engines built with the `sinopac` extra contain SinoPac's SDK, which may not be redistributed. `.github/actions/private-image` fails when GHCR serves an engine image anonymously, with or without plugins; `images` runs it before publishing and after the push (a first push creates the package). Fix: package settings → *Change visibility* → private. The terminal package may be public.

## Forks

- Workflows from fork pull requests need a maintainer's approval and get no secrets. The test jobs need none, since they use only the built-in indicators, so every test job and `security.yml` run.
- Forks never publish or deploy: `images` skips forked repositories, and the deploy jobs need it.

## Environments

`staging` and `production` require a maintainer's approval per deployment and accept only `main` (plus `v*` tags for `production`). Deploy jobs are skipped while `STAGING_SSH_HOST` / `PRODUCTION_SSH_HOST` is empty.

## Supply chain

- Third-party actions are pinned to commit SHAs with version comments; GitHub's own `actions/*` and `github/codeql-action` use major tags.
- Dependabot (`.github/dependabot.yml`) updates npm, uv, Docker base images and actions (SHA pins included). It does not see `MARKET_PLUGIN_REQUIREMENTS`: bump plugin pins yourself.
- CodeQL scans Python, JavaScript/TypeScript and the workflows (`security-extended`). On 2026-11-23 no alert was open; two are dismissed as false positives (venue-mandated HMAC signing; a redaction test logging a fake secret).

## One-time repository setup

1. Environments `staging` and `production`: required reviewers; deployment branches `main` (plus `v*` tags for `production`).
2. Settings → Actions → General: require approval for fork pull request workflows from all outside contributors.
3. Optionally protect `main` with pull requests and the test and CodeQL jobs as required checks.
4. Variables `STAGING_SSH_HOST`, `_SSH_USER`, `_DEPLOY_DIR`, `_WEB_URL`, `_HEALTH_URLS` (space-separated) and secrets `STAGING_SSH_KEY`, `STAGING_SSH_KNOWN_HOSTS` (`ssh-keyscan -H <host>`); the same with `PRODUCTION_`. Application secrets stay on the servers.
5. Optionally, [indicator plugins](#indicator-plugins-in-the-market-image) for the market image.
6. After the first publish, confirm both engine packages are private.
7. Private repositories only: code scanning, dependency review and attestations need GitHub Advanced Security and the variable `CODE_SCANNING_ENABLED=true`.

### Indicator plugins in the market image

Only the `images` job installs plugin packages; every test job uses the built-ins. Leave them unset to publish a market image with the built-ins only.

| Setting | Kind | Value |
|---|---|---|
| `MARKET_PLUGIN_REQUIREMENTS` | Actions variable | Space-separated pip requirements on one line, installed without their dependencies (list them all), e.g. `git+https://github.com/<owner>/<repo>.git@<commit>#subdirectory=<dir>`. Pin to commits: Dependabot does not update them |
| `PLUGIN_GIT_TOKEN` | Actions secret | Only for private GitHub repositories: a fine-grained token, *Only select repositories* → the plugin repositories, **Contents: Read-only** (Metadata is added automatically), with an expiry (e.g. 90 days) |
| `MARKET_IMAGE_LICENSES` | Actions variable | Optional additional SPDX expression when plugins add licences; combined with the integrated market application's `AGPL-3.0-only` baseline. Defaults to `MIT` for original components. |

- **Store** them from your checkout: `gh variable set MARKET_PLUGIN_REQUIREMENTS`, `gh secret set PLUGIN_GIT_TOKEN` (each prompts for the value). Not as environment settings: the `images` job has no `environment:`.
- **Use:** the job passes the variable as the build arg `PLUGIN_REQUIREMENTS` and the token, when set, as the BuildKit secret `plugin_git_token`. Inside one `RUN` the token rewrites `https://github.com/` URLs; it never reaches a layer, the build cache or a git config file. The build fails if `python -m joseph.indicators.discovery --check` reports a plugin that does not load.
- **Rotate** the token before it expires (GitHub emails a reminder): regenerate it with the same settings and set the secret again.

## Servers

On each host (Docker with compose), as the deploy user: create a deploy directory, copy `deploy/.env.example` to `.env` there, fill every `change-me`, and point `*_DEPLOY_DIR` at it. A deploy copies `deploy/docker-compose.yml`, pulls the commit's images with the workflow token, runs `docker compose up -d --wait` and polls the health URLs. Put TLS and domains in a reverse proxy in front of ports 3000/8000/8100.

## Release and rollback

- **Release:** `git tag v0.5.0 && git push origin v0.5.0` runs CI, publishes semver images, creates a GitHub release and, after approval, deploys to production.
- **Rollback:** Actions → **redeploy** → environment and a previous tag (`sha-<commit>` or `vX.Y.Z`). Databases are forward-compatible within a minor version.

## Split repositories

`joseph-terminal` is built to become its own public repository: `joseph-terminal/.github/workflows/ci.yml` is its standalone CI, and it carries copies of the ADRs it depends on (`joseph-terminal/docs/adr/`, identical to `docs/adr/` 0001–0003, 0007, 0008). Each engine job can move to its own repository by checking out `joseph-terminal` for the shared contracts (`JOSEPH_CONTRACTS_DIR`).
