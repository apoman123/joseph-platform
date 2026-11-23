# CI/CD

Repository: `git@github.com:apoman123/joseph-platform.git` (private). This checkout pushes with `core.sshCommand` set to the apoman123 deploy key.

All workflows live in `.github/workflows/` at the repository root, so they run on every push regardless of which project changed.

## Pipelines

| Workflow | Trigger | What it does |
|---|---|---|
| `ci-cd.yml` | every push, every PR, manual | **CI:** terminal (contracts, Prettier/ESLint, types, Vitest + coverage, boundaries, secrets, licenses, npm audit, build), terminal browser tests (DEMO + axe), both engines (ruff, mypy --strict, pytest + coverage, OpenAPI drift, licenses, pip-audit), cross-service system tests, full-stack browser tests. **CD (main and `v*` tags only, after all CI jobs pass):** build and push three images to GHCR with SBOM + provenance attestation → deploy to **staging** (main) or create a GitHub release and deploy to **production** (tags) |
| `security.yml` | push to main, PRs, weekly | CodeQL (Python, TypeScript, Actions), gitleaks over full history, dependency review on PRs (blocks high severity and GPL/AGPL), npm + Python vulnerability audit |
| `nightly.yml` | daily, manual | Live venue contract tests + connectivity probe (kept out of push CI because exchanges are external) |
| `redeploy.yml` | manual | Deploy any published tag to staging/production — the rollback path |
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

`ghcr.io/<owner>/joseph-market-engine`, `…/joseph-execution-engine`, `…/joseph-terminal`, tagged `sha-<commit>`, `latest` (main), and `vX.Y.Z` / `X.Y.Z` / `X.Y` (tags). All run as non-root with health checks. Build locally with `make images`; run the whole stack with `make compose-up` (see `deploy/`).

The terminal image bakes `NEXT_PUBLIC_*` at build time from repository variables `PUBLIC_MARKET_API_URL`, `PUBLIC_MARKET_WS_URL`, `PUBLIC_EXECUTION_API_URL`, `PUBLIC_EXECUTION_WS_URL`; leave them unset for a DEMO-only image.

## One-time repository setup

1. **Branch protection on `main`:** require PRs and the checks `Terminal — lint, types, unit, contracts, boundaries, build`, `Terminal — browser system tests (DEMO) + accessibility`, both `Engine — …` jobs, `Cross-service system tests`, `Full-stack browser tests (web + both engines)` and the CodeQL jobs.
2. **Environments:** create `staging` and `production`; give `production` required reviewers (manual approval before every production deploy).
3. **Variables** (per environment or repository): `STAGING_SSH_HOST`, `STAGING_SSH_USER`, `STAGING_DEPLOY_DIR`, `STAGING_WEB_URL`, `STAGING_HEALTH_URLS` (space-separated, e.g. `https://api.example.com/health https://exec.example.com/health https://app.example.com/`), and the same with `PRODUCTION_`. Deploy jobs are skipped while the host variable is empty, so CI and image publishing work before any server exists.
4. **Secrets:** `STAGING_SSH_KEY`, `STAGING_SSH_KNOWN_HOSTS` (output of `ssh-keyscan -H host`), and the `PRODUCTION_` pair. No application secrets go to GitHub.
5. **Indicator library access (required — CI fails until done):** the market engine installs `joseph-smc-indicators` from the private `apoman123/PineScripts` repository over HTTPS with a fine-grained personal access token. Create it under GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens**: resource owner `apoman123`, **Only select repositories** → `PineScripts`, repository permission **Contents: Read-only** (Metadata: Read-only is added automatically), nothing else, and an expiration (for example 90 days). Store the same token twice, as an Actions and a Dependabot repository secret; `gh` prompts for the value, so it stays out of shell history:

   ```bash
   gh secret set PINESCRIPTS_TOKEN -R apoman123/joseph-platform
   gh secret set PINESCRIPTS_TOKEN -R apoman123/joseph-platform --app dependabot
   ```

   Use repository secrets, not environment secrets: the CI jobs that install the engine declare no `environment:`. Dependabot reads only Dependabot secrets, both in the CI runs it triggers and for its own updates (`.github/dependabot.yml` registers the token as a `git` registry for the market engine). The composite action `.github/actions/indicators-access` masks the token and rewrites `https://github.com/apoman123/PineScripts` (that repository only) to an authenticated URL; the image build receives it as the BuildKit secret `pinescripts_token` (`RUN --mount=type=secret`), so it never enters a layer or the build cache. **Rotation:** before the expiry (GitHub emails a reminder), regenerate the token with the same settings and run both `gh secret set` commands again; the old value stops working at once. **After switching from the old deploy key** (once CI is green): `gh secret delete PINESCRIPTS_DEPLOY_KEY -R apoman123/joseph-platform`, the same with `--app dependabot`, then `gh repo deploy-key list -R apoman123/PineScripts` and `gh repo deploy-key delete <id> -R apoman123/PineScripts` for `joseph-platform-ci`. To adopt new indicator ports, bump `rev` under `[tool.uv.sources]` in `joseph-market-engine/pyproject.toml` and run `uv lock`.
6. **Packages:** after the first publish, set the GHCR packages' visibility (private by default).
7. **Private-repo limits:** CodeQL and dependency review need GitHub Advanced Security on private repositories — enable it and set the variable `CODE_SCANNING_ENABLED=true` (they run automatically on public repos). GitHub artifact attestations run only on public repos (or Enterprise Cloud); images still carry BuildKit SBOM and provenance.

## Server preparation (per environment)

```bash
# On the host (Docker + compose plugin installed), as the deploy user:
mkdir -p ~/joseph && cd ~/joseph
# Copy deploy/.env.example from the repo to .env and fill every change-me value.
# Then point STAGING_DEPLOY_DIR (or PRODUCTION_DEPLOY_DIR) at ~/joseph.
```

Each deploy copies `deploy/docker-compose.yml`, logs in to GHCR with the short-lived workflow token, pulls the commit's images, runs `docker compose up -d --wait` (health-checked) and then polls the health URLs. Put TLS and the domain names in a reverse proxy (Caddy, nginx, a cloud load balancer) in front of ports 3000/8000/8100.

## Rollback

Actions → **redeploy** → choose the environment and a previous tag (`sha-<commit>` or `vX.Y.Z`). Databases are forward-compatible within a minor version; strategy versions are immutable.

## Releasing

```bash
git tag v0.5.0 && git push origin v0.5.0
```

The tag runs the full CI, publishes semver images, creates a GitHub release (generated notes + contracts bundle) and, after approval, deploys to production.

## Split repositories

If `joseph-terminal` becomes its own public repository, `joseph-terminal/.github/workflows/ci.yml` is its standalone CI. Each engine job in `ci-cd.yml` can move to its own repo by checking out `joseph-terminal` for the shared contracts (`JOSEPH_CONTRACTS_DIR`).
