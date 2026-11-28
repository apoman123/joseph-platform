# Heatmaps

A heatmap tab shows a whole market at a glance: each symbol is a tile whose
area is one metric (by default market capitalization) and whose colour is
another (by default the one-day change). Open one with **+** → **Heatmap**
in the tab strip. A tab remembers its data set, options and display settings
in the workspace, so they survive reloads, exports and share links.

The use cases and options follow the heatmaps of
[TradingView](https://www.tradingview.com/support/solutions/43000766446-tradingview-heatmaps-from-global-trends-to-details/)
(stocks, ETFs and crypto coins, size and colour metrics, sector grouping,
legend filters, zoom into a group, display settings). This page describes the
data behind each option and what is not available from free sources.

## Heatmap types and data sets

| Type | Data sets | Default size | Grouping |
|---|---|---|---|
| Stocks | United States: **S&P 500**, **Nasdaq 100**, **Nasdaq Composite** (Nasdaq-listed), **NYSE**, **All US stocks**. Taiwan: **TWSE listed**, **TPEx listed** | Market cap | Sector (nested industries), or no group |
| ETFs | **US ETFs**, **Taiwan ETFs** (TWSE and TPEx) | Turnover 1D (AUM is unavailable) | Asset class, or no group |
| Crypto coins | **All coins**, **Excluding Bitcoin**, **Excluding stablecoins**, **DeFi** | Market cap | None (one level) |

Tiles are sorted from the largest to the smallest. Large data sets are capped
at the largest members by the default size: 1,000 tiles for Nasdaq Composite,
NYSE, All US stocks and TWSE, 900 for TPEx, 500 for US ETFs, 400 for Taiwan
ETFs and 250 coins; index data sets show all members. US and Taiwan data sets
are never mixed, so USD and TWD sizes are never added together.

## Size, colour and grouping options

**Size by.** Stocks: Market cap, Volume 1D/1W/1M, Price × Volume (Turnover)
1D/1W/1M, Mono size. ETFs: Assets under management (always unavailable, see
below) and the same volume and turnover options. Crypto: Market cap, Fully
diluted market cap, Volume in USD 24h, Mono size. *Mono size* gives every tile
the same area; the default size then only sets the order.

**Color by.** Change 1D (stocks, ETFs) or Change 24h (crypto) by default; Change
1h and 4h; Performance 1W, 1M, 3M, 6M, YTD and 1Y; Pre-market and Post-market
change (US stocks); Relative volume; Volatility 1D (24h for crypto); Gap.
Rising values are green, falling ones red and values near zero grey; stronger
moves are deeper shades. Relative volume and volatility have no direction and
use a single-hue intensity scale. A tile without a value is a neutral grey.

**Group by.** Stocks are grouped by sector with their industries nested inside
(thin separators); ETFs by an asset class (Equity, Fixed income, Commodity,
Currency, Crypto, Multi-asset/Alternative) that is **classified from the fund
name** by keywords, not an official classification. *No group* draws a flat
map.

An option that the answering sources cannot supply is disabled in its menu
and states why. Nothing is estimated to fill the gap.

## Reading and using the map

- **Legend.** On every load the legend splits the range of the colour metric
  into segments (for the daily change, for example, −3 % … +3 % in seven
  steps). Click a segment to hide its tiles; click again to show them.
  Segments toggle independently.
- **Hover card.** Symbol, name, price, the exact size and colour values, the
  group header (sector › industry) and the other symbols of the same
  industry or group with their colour values.
- **Zoom.** Click a group header to show only that group, with the same size
  and colour options; the breadcrumb goes back to the whole market.
- **Open a chart.** Click a tile (or focus it and press Enter) to open a
  candlestick tab of the instrument. Right-click for *Open chart* and *Add to
  watchlist*. A symbol that no configured venue lists says *No chart on
  configured venues* (US charts need Alpaca keys, Taiwan charts need Fugle).
- **Display settings** (cogwheel): tile title (none, ticker or description), a
  generic letter badge as logo, the value shown on tiles (none, the colour
  value, the price or the size value), and the colour scheme (Classic,
  Color-blind, Monochrome).
- **Share** (camera): download the map as PNG, copy the image, or copy a link
  to the current view (a workspace share link with this tab active).
- **Fullscreen**, and an **automatic refresh** every minute with the as-of time
  and the source of each part in the footer. While a slow source is still
  loading the map refreshes sooner and marks the coverage as partial.

## Data sources and caching

All heatmap sources are free and need no key; configured market-data feeds
improve them when present. The engine never waits more than about four
seconds for a request: slow sources keep loading in the background and the
response carries the cached or partial data, marked as pending.

| Part | Source | Cached | Notes |
|---|---|---|---|
| US members, sector, industry, market cap | [Nasdaq.com stock screener](https://www.nasdaq.com/market-activity/stocks/screener) (`api.nasdaq.com`, unofficial, delayed) | 24 h; 10 min while it is also the quote source | One sector taxonomy (Nasdaq.com) for every US data set; a listing without a sector is *Miscellaneous*. Preferred shares, warrants, units and rights are skipped |
| S&P 500 members | Wikipedia *List of S&P 500 companies* (constituents table) | 24 h | Membership only; sectors still come from the screener |
| Nasdaq-100 members | Nasdaq.com Nasdaq-100 list | 24 h | |
| US quotes with Alpaca keys | Configured Alpaca feed: selected-symbol snapshots (latest trade, current and previous daily bar) | 15 s | Change 1D, Gap, Volatility 1D, Volume and Turnover 1D, Pre-/Post-market change. IEX is one exchange, not the consolidated tape |
| US history with Alpaca keys | Alpaca multi-symbol daily bars, split-adjusted, about 400 days | Per New York trading day | Performance 1W–1Y and YTD, Relative volume (today over the 10-session average), Volume and Turnover 1W/1M (trailing 5 and 21 sessions). At most 40 bar requests per minute |
| US quotes without keys | Nasdaq.com screener delayed last sale, change and volume | 10 min | Turnover 1D is estimated as last sale × volume; metrics needing open/high/low or history are unavailable |
| Taiwan companies, industry, issued shares | TWSE `t187ap03_L` and TPEx `mopsfin_t187ap03_O` open data | 24 h | Industry codes mapped to English and Chinese names; market cap is **derived** as issued shares (or paid-in capital ÷ par value) × last price |
| Taiwan quotes | Configured Taiwan feed when its venue is enabled; otherwise TWSE `STOCK_DAY_ALL` and TPEx daily close quotes (official end of day) | 10 min | End-of-day data gives Change, Gap, Volatility, Volume and traded value; no history-based metrics |
| Taiwan ETFs | ETF codes (`00…`) of the same daily files | 10 min | Asset class classified from the fund name |
| US ETFs | ETF listings of the configured US venues with Alpaca quotes | as US quotes | Unavailable without Alpaca keys: no keyless source lists US ETF quotes |
| Crypto coins | [CoinGecko](https://www.coingecko.com/en/api) `coins/markets`, top 500 by market cap; categories *stablecoins* and *decentralized-finance-defi* | 60 s (stablecoin list 1 h) | Change 1h/24h, Performance 1W/1M/1Y, Volatility 24h from the 24h high and low. 4h, 3M, 6M and YTD windows are not published. After HTTP 429 the engine pauses requests for 90 s and serves the cached map |
| Crypto charts | Enabled crypto venues | 10 min | The Binance USDⓈ-M perpetual `<SYM>USDT` first, else a spot USDT/USD/USDC pair of another enabled venue |

Not available from these sources, and therefore disabled with a reason:
fund assets under management (ETF *AUM*); intraday Change 1h and 4h for stocks
and ETFs; history-based metrics for Taiwan and for US stocks without Alpaca
keys; crypto 4h, 3M, 6M and YTD performance.

The engine in DEMO-only mode (`JOSEPH_DEMO_ONLY=true`) disables the public
sources. The terminal's DEMO mode (no engines) shows a synthetic, clearly
labelled DEMO data set of fictional companies, funds and coins.

## API and MCP

- `GET /v1/heatmap/catalog` lists the data sets with their availability and
  the default per type.
- `GET /v1/heatmap?type=stocks|etfs|crypto&dataset=<id>&limit=<n>` returns the
  tiles (largest default size first) with every available metric as a decimal
  string, group and industry, the instrument to chart (or the reason there is
  none), the size, colour and grouping options with availability and reasons,
  provenance per part, per-source errors and a `pending` flag.

Both are authenticated like the rest of the market API and are exposed to MCP
clients as `market_get_heatmap` and `market_get_heatmap_catalog`. The response
models are `HeatmapSnapshot` and `HeatmapCatalog` in
`joseph-terminal/packages/contracts/schemas/python-scripts.schema.json`.

## Tests

Engine tests replay recorded responses of every source
(`joseph-market-engine/fixtures/heatmap`) and hand-written Alpaca multi-symbol
bars; they never call the network. `make test-network` adds a live smoke test
of the keyless sources. Terminal tests check the treemap layout (no overlap,
areas proportional to the size metric, tiles inside their group), legend
segmentation and filters, colour schemes, zoom and settings persistence, and a
DEMO browser test with accessibility checks.
