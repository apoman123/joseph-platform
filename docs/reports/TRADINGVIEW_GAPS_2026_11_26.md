# TradingView comparison and implementation roadmap

Reviewed 2026-11-26. This comparison combines the current repository with
TradingView's official documentation. TradingView capabilities vary by plan,
market-data entitlement and platform. The original 94-item design audit is a
smaller project checklist, not full product parity.

| Workflow | Joseph today | Remaining gap and suggested order |
|---|---|---|
| Receive strategy alerts away from the terminal | Durable outbox, quiet hours, filters, Telegram, LINE, email and webhooks; this batch adds official WhatsApp templates, encrypted browser/mobile push, destination deletion and explicit delivery tests | **First batch implemented.** Actual delivery requires operator credentials/device permission. Provider acceptance is visible; read receipts and provider delivery webhooks are not implemented. |
| Set a quick alert on price, an indicator value or a drawing | Alerts originate from strategy signals and their channel policies | **Next:** standalone crossing/above/below alerts with once/bar-close frequency, expiration, chart/context-menu creation, edits, pause and restart-safe condition state. TradingView supports price, technical and drawing alerts. [Alert types](https://www.tradingview.com/support/solutions/43000595315-how-to-set-up-alerts/), [configuration](https://www.tradingview.com/support/solutions/43000763312-learn-how-to-configure-alerts/). |
| Alert on a whole watchlist | Subscription filters select existing strategy signals; no watchlist condition evaluator | After standalone conditions: server-side watchlist alert expansion, membership changes and per-symbol deduplication. [TradingView alert configuration](https://www.tradingview.com/support/solutions/43000763312-learn-how-to-configure-alerts/). |
| Develop custom studies in the browser | Parameterized built-ins, installed Python indicator plugins, My scripts/My collection and a strategy rule builder | A browser editor/compiler sandbox, saved script versions, custom study authoring and publish/install workflows. The current My scripts tab is not Pine Script compatibility. [Pine documentation](https://www.tradingview.com/pine-script-docs/welcome/). |
| Explore market/volume structure | Candles, OHLC bars, line, area and Heikin Ashi; volume/delta and backend studies | Renko, Kagi, point-and-figure, range/line-break charts, volume footprint, session/fixed/visible-range volume profile and TPO. Validate feed depth and price/volume provenance before adding transforms. [TradingView chart types](https://www.tradingview.com/support/solutions/43000703407-chart-types-available-on-tradingview/). |
| Screen across stocks, ETFs and custom study values | Qualified-perpetual screening, filter presets and watchlists | Broader asset-class/fundamental filters and arbitrary custom-study screening. [Pine Screener](https://www.tradingview.com/support/solutions/43000742436-tradingview-pine-screener-key-features-and-requirements/). |
| Practice in a historical market | Bar replay with replay provenance, historical overlays and trading disabled | A separate replay paper account, replay orders, saved replay sessions and synchronized multi-chart replay. Preserve the boundary between replay and live execution. [Bar Replay](https://www.tradingview.com/support/solutions/43000712747-bar-replay-how-and-why-to-test-a-strategy-in-the-past/). |
| Research strategies over long histories | Backend backtests, immutable strategy versions and reports | Deeper data-dependent test ranges, walk-forward/parameter searches, explicit transaction-cost scenarios and research comparison views. Depth depends on each free feed, not the language used to fetch it. [TradingView strategy testing](https://www.tradingview.com/pine-script-docs/faq/strategies). |
| Continue on another device | Local saved tabs/layouts, import/export, linked panes and pop-outs; this batch adds an installable push shell | Authenticated cloud layout/watchlist/script sync, conflict resolution and mobile-specific navigation. The PWA shell is not cloud sync or a native mobile trading app. |
| Organize broader research | Chart studies, watchlists, indicators and strategy reports | Economic/earnings/dividend calendars, fundamentals/financial statements, news and community publishing/social workflows. These require separate source and product decisions; they are not implied by adding more data-feed providers. |

## Delivered alert increment

- Official Meta WhatsApp Cloud API with an approved one-body-parameter template;
  Telegram remains supported and gains a testable setup flow.
- Standard Web Push with encrypted payloads and VAPID authentication, a service
  worker and Home Screen manifest. Permission is requested only from a user click.
- Tenant-scoped subscriptions, private device keys omitted from API responses,
  tests that cannot execute trades, durable retry/status reporting and automatic
  disabling of expired push registrations.
- Channel policy support in Python strategy validation and shared TypeScript
  contracts. Existing strategy versions keep their existing allowed channels.

The first increment deliberately completes delivery for existing strategy alerts.
Standalone price/drawing alerts and the other roadmap rows remain unimplemented;
none is claimed as complete by the prior audit or this report.

Setup, key generation, template requirements and device constraints are documented
in [README: Alert delivery](../../README.md#alert-delivery). WhatsApp may incur
[Meta messaging charges and template requirements](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages/).
Apple mobile Web Push requires a Home Screen web app on iOS/iPadOS 16.4 or later;
see [WebKit's requirements](https://webkit.org/blog/13878/).
