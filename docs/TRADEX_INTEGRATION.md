# TradeX integration

Joseph integrates the public-source and drawing functionality of
[open-source-tradingview](https://github.com/saifullahshafin/open-source-tradingview)
without adopting its standalone HTTP server, account system or execution path.
The reviewed upstream revision is
`f8532c31af5060ee4dbafcce11fef0ea8f984aeb`.

## Architecture

- Next.js remains the terminal. `ChartPort` remains the chart boundary.
- Drawing plugins call the renderer-neutral core of
  `lightweight-charts-drawing@0.6.2`, the version pinned by upstream. All
  86 kinds use its placement, geometry, hit testing and drag rules.
- Joseph retains canonical drawing identities, instrument ownership, decimal
  prices, locking, visibility, import/export, persistence and command history.
  Existing drawings retain their original tool IDs and geometry.
- Python remains the only user script language. Indicator, strategy, library,
  filter and alert scripts retain their separate abstract classes and isolated
  server-side runtime. No Pine editor, PineTS engine or script scraping is enabled.
- Market sources implement the existing `MarketDataPort`. Candle stores, source
  provenance, session calendars, aggregation, recovery and the existing market
  WebSocket stay in the market engine. The browser does not connect to exchanges.
- Trading remains in the execution engine. Adding a data provider does not add
  a trading account, broker mapping or permission to place orders there.

The complete reference snapshot is preserved under
`joseph-terminal/third_party/tradex/`, including its original license and notices.
Its files are reference material, not a second application served by Joseph.
`pnpm upstream:check` verifies every preserved file against the pinned SHA-256
inventory. Changes belong in Joseph adapters and features rather than in that
snapshot. Upstream installers, remote restart commands and sign-in services are
not run by the application.

## Native data providers

| Provider | Markets | Current prices | History | Credentials |
| --- | --- | --- | --- | --- |
| Binance | USDⓈ-M perpetuals and listed RWA contracts | Native WebSocket | Native REST, paged history and optional archive | None for public data |
| Bybit | Spot, linear/inverse perpetuals and dated futures | Native candle WebSocket | Native REST, backward pagination | None for public data |
| OKX | Listed spot markets, including available tokenized gold | Explicit REST polling | Native REST pages | None |
| Coinbase Exchange | Listed spot markets | Explicit REST polling | Native REST pages; sparse trading intervals may have no candle | None |
| Kraken | Listed spot markets | Explicit REST polling | Newest 720 native candles per timeframe; older history is unavailable | None |
| Deriv | FX, commodities, indices and crypto reference quotes | Native OHLC WebSocket | Bounded reusable WebSocket request pool | None |
| Alpaca IEX | Nasdaq, NYSE and Cboe stock/ETF listings | Existing Alpaca WebSocket | Existing Alpaca history | `JOSEPH_ALPACA_KEY_ID`, `JOSEPH_ALPACA_SECRET_KEY` |
| Fugle | TWSE and TPEx stocks/ETFs | Paced REST polling | Existing native history and recorded minutes | `JOSEPH_TW_DATA_SOURCE=fugle`, `JOSEPH_FUGLE_API_KEY` |
| U.S. Treasury | Published yield curves | Daily publication | Official daily series | None |
| FRED | Additional rate and spread series | Daily publication | Official daily observations | Optional `JOSEPH_FRED_API_KEY` |

The upstream OKX, Coinbase and Kraken plugins use REST polling; they do not
subscribe to exchange WebSockets. Joseph reports that distinction in Data health.
An idle source has no active price subscription. Successful discovery alone does
not prove that live quotes are flowing. Discovery errors appear in Data health,
and failed providers retry in the background.

Unfiltered interactive symbol search returns metadata already received within
one second while slow sources continue loading. Requested-provider searches and
authoritative filter universes retain exact provider identity and completeness
requirements. Metadata loading has a twenty-second source deadline; one failed
provider does not remove symbols from providers that answered.

Bybit inverse candle volume is converted from the native turnover field to base
units. USD and USDT markets remain distinct. PAXG is a gold token, not spot gold.
Deriv reference quotes retain their own identities and do not substitute for
Alpaca stock prices. Deriv supplies no exchange volume: the canonical volume is
zero and its capabilities disclose the omission. Quiet/closed Deriv intervals
are sparse; answered history windows provide coverage without synthetic candles.

Extra timeframes continue to aggregate in Python from native intervals that tile
the target interval. Unsupported seconds histories are rejected rather than
relabeled from minute candles. Source history limits remain visible as missing
history and never trigger a switch to another provider.

## Migrating a deployment

Update an existing `JOSEPH_ENABLED_VENUES` override to select the new sources:

```dotenv
JOSEPH_ENABLED_VENUES=binance,bybit,okx,coinbase,kraken,deriv,twse,tpex,nasdaq,nyse,cboe,treasury
JOSEPH_ALPACA_FEED=iex
JOSEPH_ALPACA_KEY_ID=your-alpaca-key-id
JOSEPH_ALPACA_SECRET_KEY=your-alpaca-secret-key
JOSEPH_TW_DATA_SOURCE=fugle
JOSEPH_FUGLE_API_KEY=your-fugle-market-data-key
JOSEPH_FUGLE_ADJUSTED_DAILY=true
# Optional additional daily series:
JOSEPH_FRED_API_KEY=your-fred-api-key
```

Leave optional keys empty to omit those key-backed sources. Keep provider keys
on the backend; none belongs in a `NEXT_PUBLIC_*` variable. Restart the market
engine after changing source configuration. No crypto public-source key is
required. CCXT remains available to explicit adapter-factory compatibility
callers; the running application selects native adapters.

Hyperliquid, Lighter, Bitget and MEXC native adapters remain available for explicit
configurations and regression coverage. They are absent from the new default
data-provider selection. Their existing trading adapters remain supported.

## Drawing settings

Click a left-hand group icon to choose a drawing; there is no separate arrow
target. The toolbar shows the upstream drawing catalog while legacy drawing
plugins remain registered to read existing workspaces. Select a drawing to edit
its basic style, lock, visibility, order or text. Its settings button opens
source-specific style properties, exact UTC/price coordinates and content.

Table drawings have editable cells and bounded row/column controls. Image
drawings accept embedded PNG, JPEG or WebP files up to 2 MB and 2000 pixels per
side. Embedded images travel with the drawing; the image loader rejects remote
URLs and file paths. Geometry and content changes use the same canonical undo
history as every other drawing operation.

## License and source

The integrated application is AGPL-3.0-only, as approved for this integration.
Original MIT file notices remain intact, and original Joseph license texts are
preserved. The terminal footer links visibly to TradeX by Saifullah Shafin. The license
page also credits [lightweight-charts-drawing by deepentropy](https://github.com/deepentropy/lightweight-charts-drawing)
for drawing geometry and retains the TradingView Lightweight Charts attribution.
The license page includes:

> Based on TradeX by Saifullah Shafin - https://github.com/saifullahshafin/open-source-tradingview

This is a modified application with Joseph branding. Upstream trademarks and
logos are not used as Joseph branding. The source and build instructions are
available in [the Joseph repository](https://github.com/apoman123/joseph-platform).
Container images preserve the applicable license and notice files. CI verifies
the approved aggregate license, notices and pinned upstream inventory, and
rejects Pine runtimes in production dependencies.

Historical Taiwan price adjustment, intraday limits and cache behavior are
documented in [Market data scope](MARKET_DATA_SCOPE.md#corporate-action-price-basis).

## Security analysis boundary

The immutable `third_party/tradex` snapshot is an unserved source reference. Its
Python server, setup helpers, browser pages and prebuilt alternative engines are
not imported by Joseph, mounted as static files, or copied into the isolated
script runner. Runtime drawing behavior comes through the reviewed Joseph
adapter and installed drawing-core dependency; provider behavior runs through
the market engine's ports.

CodeQL analyzes Joseph's active adapters, application and engines. It excludes
only that pinned reference tree, whose original server/setup/browser flows
otherwise produce findings unrelated to Joseph's deployed attack surface.
The dependency boundary check forbids runtime imports from the reference tree.
Secret scanning, source checksum validation and license checks still include
the snapshot; no active application directory is exempted.
