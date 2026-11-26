# Alert delivery verification — 2026-11-26

## Scope

Official WhatsApp Cloud API templates, encrypted Web Push, Telegram setup and
explicit test delivery, destination deletion, tenant isolation, private browser
registration storage, shared contracts, device setup UI and deployment assets.
The [TradingView comparison](TRADINGVIEW_GAPS_2026_11_26.md) records the broader
remaining gaps; standalone price/drawing alerts are not part of this increment.

## Local regression results

| Suite | Passed | Skipped | Evidence |
|---|---:|---:|---|
| Market engine | 1,990 | 51 | Full pytest unit/integration/contract/architecture/property/security/chaos suite with coverage |
| Execution engine | 976 | 9 | Full pytest suite with coverage; no execution behavior changed |
| Terminal | 1,032 | 0 | Vitest unit/component/contract/architecture suites |
| Cross-service system | 118 | 0 | Both Python engines running together |
| DEMO browser | 50 | 0 | Production Next.js build, Chromium, accessibility and existing chart/trading workflows |
| Full-stack browser | 18 | 0 | Production terminal and both real Python engines with isolated mock-market data |
| **Total** | **4,184** | **60** | Each full suite counted once; focused reruns are excluded |

The skips are opt-in network, sandbox or credential-dependent cases. Real
Telegram/WhatsApp recipients and Apple/Android devices were not available for
end-to-end handset delivery. No real notification was sent to a user. Provider
transport tests mock external I/O while executing the actual adapters and SDK.

Ruff formatting/lint, pydoclint, strict mypy, Prettier, ESLint, Stylelint,
TypeScript checks, generated contracts/OpenAPI checks, dependency boundaries,
secret scans, license checks and production dependency vulnerability audits
passed. A production terminal container was built and verified to serve the
service worker, install manifest and both PNG icons. The CI/CD image job now
performs the same asset smoke check on the published image digest.

## Notification-specific evidence

- `tests/integration/alerts/test_mobile.py`: approved Meta template payload,
  provider acceptance, permanent errors, rate limits/server retries, secret
  redaction, real SDK AES128GCM/VAPID encryption, multilingual payload size,
  expired-device disabling, redirect refusal, tenant name collisions, legacy SQL
  row compatibility, private registration preservation and durable test delivery
  after recreating the outbox adapter.
- `tests/integration/alerts_api/test_subscription_api.py`: authenticated
  create/list/test/status/delete through the full application, channel contracts,
  cross-tenant denial, environment-file precedence and server secret redaction.
- `tests/unit/alerts/test_routing.py`: new mobile channels obey existing strategy
  opt-in policies, filters, quiet hours and critical-severity bypass.
- `device-push.test.ts`: explicit permission requests, denial/unsupported contexts,
  rollback after persistence failure, registration reuse, preserved filters and
  removing only the current device's local subscription.
- `notifications-panel.test.tsx`: configuration hints, save/delete, explicit test
  enqueue/status polling and reflecting successful server deletion even when
  local device unsubscribe fails.
- `alert-service-worker.test.ts`: executes the production worker's push and click
  handlers, including malformed data fallback, notification tags, window focus
  and refusal to follow payload-controlled external URLs.
- `e2e-fullstack/notifications.spec.ts`: real service-worker registration and
  browser crypto, UI → Python persistence, omitted private keys, permission
  denial and WhatsApp storage/unconfigured-channel behavior. Only permission and
  the external PushManager subscription boundary are mocked.

Browser regression testing caught and corrected wrapping that made existing
alert-table actions overlap. Parallel browser runs also exposed a shared
Playwright trace-directory race; full-stack artifacts now have their own folder,
and CI uploads that folder on failure.

## Operator acceptance

Follow [README: Alert delivery](../../README.md#alert-delivery) to configure the
Telegram bot, Meta sender/template or VAPID key pair. Restart the market engine,
register each destination/device, enable its channel in the strategy's saved
notification policy and activate that version. Use **Send test** and confirm
receipt on the intended device. Provider acceptance is not a read receipt.
Meta approval, permissions, opt-in recipients and charges remain external
requirements. iOS/iPadOS requires a Home Screen app with notification permission.

The push to the remote repository runs the checked-in CI/CD and Security
workflows. Their final run links and outcomes are reported in the delivery
message; deployment jobs run only for configured environments and release tags.
