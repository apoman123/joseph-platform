# CI/CD

Repository: `git@github.com:apoman123/joseph-platform.git` (public, MIT); this checkout pushes with `core.sshCommand` set to the apoman123 key. Workflows live in `.github/workflows/` and run on every push, whichever project changed.

## Pipelines

| Workflow | Trigger | What it does |
|---|---|---|
| `ci-cd.yml` | push, PR, manual | **CI:** every test tier of the terminal and both engines, plus lint, types, coverage, contract and OpenAPI drift, boundaries, secrets, licenses and audits. **CD** (main and `v*`, after CI): three GHCR images with SBOM and provenance → **staging** (main) or a release and **production** (tags) |
| `security.yml` | push to main, PR, weekly | CodeQL (Python, TypeScript, Actions), gitleaks over full history, dependency review on PRs (blocks high severity and GPL/AGPL), npm and Python audits |
| `nightly.yml` | daily, manual | Live venue tests and connectivity probe |
| `redeploy.yml` | manual | Deploy a published tag (rollback) |
| `dependabot.yml` | weekly | npm, uv (×3), Docker base images, GitHub Actions |

```text
push ─► terminal ─► terminal-e2e ─┐
     ├► engines (×2) ─► system ───┤
     └► fullstack ◄──(terminal, engines)
                                  ▼ (main / v* only)
                           images (×3, GHCR) ─► deploy-staging (main)
                                              ─► release ─► deploy-production (v*)
```

## Images

`ghcr.io/<owner>/joseph-{market-engine,execution-engine,terminal}`, tagged `sha-<commit>`, `latest` (main) and `vX.Y.Z` / `X.Y.Z` / `X.Y` (tags); non-root with health checks. Locally: `make images`, `make compose-up` (see `deploy/`). The terminal image bakes `NEXT_PUBLIC_*` from the repository variables `PUBLIC_MARKET_API_URL`, `PUBLIC_MARKET_WS_URL`, `PUBLIC_EXECUTION_API_URL`, `PUBLIC_EXECUTION_WS_URL`; unset means a DEMO-only image.

## One-time repository setup

1. **Branch protection on `main`:** require PRs and the checks `Terminal — lint, types, unit, contracts, boundaries, build`, `Terminal — browser system tests (DEMO) + accessibility`, both `Engine — …` jobs, `Cross-service system tests`, `Full-stack browser tests (web + both engines)` and the CodeQL jobs.
2. **Environments** `staging` and `production`: required reviewers (you) on both and deployment branches limited to `main` (and `v*` tags for `production`).
   **Fork pull requests:** Settings → Actions → General → require approval for all outside contributors. Forks get no secrets, so their market-engine jobs stop at the indicator step with a "Fork pull request" error; verify such changes locally (`make ci`). Forks never publish images or deploy.
3. **Variables:** `STAGING_SSH_HOST`, `_SSH_USER`, `_DEPLOY_DIR`, `_WEB_URL`, `_HEALTH_URLS` (space-separated), and the same with `PRODUCTION_`. Deploys are skipped while the host is empty.
4. **Secrets:** `STAGING_SSH_KEY`, `STAGING_SSH_KNOWN_HOSTS` (`ssh-keyscan -H host`) and the `PRODUCTION_` pair. No application secrets go to GitHub.
5. **Indicator library access** (below); CI fails until done.
6. **Packages:** the engine images contain the private indicator library, so `joseph-market-engine` and `joseph-execution-engine` must stay private packages; the images job refuses to publish into, and fails after creating, a package anyone can pull (`.github/actions/private-image`). `joseph-terminal` may be public.
7. **Code scanning:** on the public repository CodeQL, dependency review and artifact attestations run automatically (a private repository needs GitHub Advanced Security plus the variable `CODE_SCANNING_ENABLED=true`).

### Indicator library access

The market engine installs `joseph-smc-indicators` from the private `apoman123/PineScripts` over HTTPS with a fine-grained token:

- **Create:** GitHub → Settings → Developer settings → Fine-grained tokens. Resource owner `apoman123`, *Only select repositories* → `PineScripts`, **Contents: Read-only** (Metadata is added automatically), nothing else, with an expiry (e.g. 90 days).
- **Store** it as a repository secret for Actions and for Dependabot (`gh` prompts for the value, keeping it out of shell history):

  ```bash
  gh secret set PINESCRIPTS_TOKEN -R apoman123/joseph-platform
  gh secret set PINESCRIPTS_TOKEN -R apoman123/joseph-platform --app dependabot
  ```

  Not environment secrets: the jobs that install the engine declare no `environment:`. Dependabot reads only Dependabot secrets, in the CI runs it triggers and for its own updates (`.github/dependabot.yml` registers a `git` registry).
- **Use:** `.github/actions/indicators-access` masks the token and rewrites only `https://github.com/apoman123/PineScripts` to an authenticated URL. Image builds get it as the BuildKit secret `pinescripts_token`, so it never enters a layer or cache.
- **Rotate** before expiry (GitHub emails a reminder): regenerate with the same settings and rerun both `gh secret set` commands.
- The SSH deploy key used before 2026-11-23 is retired: its secret and the PineScripts deploy key are deleted.
- **Adopt new indicator ports:** bump `rev` under `[tool.uv.sources]` in `joseph-market-engine/pyproject.toml` and run `uv lock`.

## Servers

On each host (Docker + compose), as the deploy user: create `~/joseph`, copy `deploy/.env.example` to `~/joseph/.env`, fill every `change-me`, and point `*_DEPLOY_DIR` at it. A deploy copies `deploy/docker-compose.yml`, pulls the commit's images with the workflow token, runs `docker compose up -d --wait` and polls the health URLs. Put TLS and domains in a reverse proxy in front of ports 3000/8000/8100.

## Release and rollback

- **Release:** `git tag v0.5.0 && git push origin v0.5.0` runs CI, publishes semver images, creates a GitHub release (notes and contracts bundle) and, after approval, deploys to production.
- **Rollback:** Actions → **redeploy** → environment and a previous tag (`sha-<commit>` or `vX.Y.Z`). Databases are forward-compatible within a minor version.

## Split repositories

`joseph-terminal` is built to become its own public repository: `joseph-terminal/.github/workflows/ci.yml` is its standalone CI, and it carries copies of the ADRs it depends on (`joseph-terminal/docs/adr/`, identical to `docs/adr/` 0001–0003, 0007, 0008). Each engine job can move to its own repository by checking out `joseph-terminal` for the shared contracts (`JOSEPH_CONTRACTS_DIR`).
