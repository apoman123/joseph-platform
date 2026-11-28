# Market data scope

Market data providers and trading destinations are configured independently.
The market engine supplies instrument metadata, quotes, candles, trades, and
books; the execution engine keeps its existing venue and broker adapters. A
missing or stale market quote must continue to block an order through the risk
gateway. It must not silently substitute a price from another market.

The user-provided `recommended_market_data_feeds_multitimeframe.md` adds
timeframe requirements to the free or low-cost development stack selected from
`recommended_market_data_feeds.md`. The provider inventory below records what
this repository currently implements. A provider appearing in the reference file is eligible
for future implementation; its mention alone does not mean this repository
offers it today.

| Asset | Implemented market data | Access and limits |
|---|---|---|
| Crypto and tokenized assets | Native Binance, Bybit, OKX, Coinbase Exchange and Kraken | Public metadata, history and quotes; Binance and Bybit use WebSocket candles, the other three use explicit REST polling. Supported listed tokenized assets retain their exchange identities. |
| FX, commodities and reference indices | Deriv public API | Public WebSocket history requests and candle subscriptions; reference quotes, with no reported exchange volume. |
| US stocks and ETFs | Alpaca IEX | Free Alpaca key required; IEX is one exchange, not consolidated SIP. |
| Taiwan stocks and ETFs | Fugle free-tier per-symbol REST | Fugle key required for 1m history from 2023-05-23 and current-session REST candles/quotes. Taiwan market data stays disabled without the key. |
| US Treasury rates | U.S. Treasury daily par and real yields; optional FRED daily rates and spreads | FRED key required for distinct `FRED_*` IDs. Published daily; not a live bond quote or tradable price. |
| Heatmap metadata and fallback quotes | Nasdaq.com screener and Nasdaq-100 list, Wikipedia S&P 500 constituents, TWSE/TPEx open data, CoinGecko coin markets | Keyless; unofficial (Nasdaq.com) or delayed/end-of-day quotes, cached 60 s to 24 h. Used only by heatmaps, never as a chart or order price. See [Heatmaps](HEATMAPS.md). |
| Demo only | Mock venue | Synthetic data only when `JOSEPH_DEMO_ONLY=true`; excluded from live market-data venues. |

SinoPac Shioaji is no longer selectable or constructed as a market-data source
by the market engine. The market engine no longer installs its SDK extra or
reads its data-feed credentials. Its trading adapter remains in the execution
engine.

## Heatmap data

Heatmaps combine the configured feeds with keyless public lists. US sector,
industry and market capitalization come from the Nasdaq.com screener for every
US data set (one taxonomy); index members come from Wikipedia (S&P 500) and
Nasdaq.com (Nasdaq-100). With Alpaca keys, US tiles use Alpaca snapshots and
split-adjusted daily bars of the configured feed (IEX stays labelled IEX);
without them the screener's delayed values are shown and labelled, and the
metrics that need history are unavailable. Taiwan tiles use the configured
Taiwan feed when its venue is enabled and otherwise the official end-of-day
files; their market capitalization is derived from issued shares and labelled
as derived. Crypto tiles use CoinGecko. These values are never used as chart,
screener or order prices, and a missing value stays missing.

## Closed-market display and chart volume

A loaded chart uses its instrument's exchange calendar to distinguish expected
silence from missing updates. After a session closes, its provenance badge and
market panel show **Market closed**, and the chart suppresses the elapsed-time
stale-data banner. This includes weekends, fetched holiday closures, and early
closes. The first minute after the next session opens allows the feed to resume;
after that, missing updates show the stale warning again. Continuous markets
retain stale-data detection throughout weekends. Unavailable feeds, failed
history loads, synthetic data, and replay retain their own status.

This is a display policy. It does not refresh an old quote or relax execution
risk checks. The backend continues to reject new exposure using stale quotes.

The chart header shows **V** for the latest candle or the candle selected by the
crosshair or replay cursor. Taiwan volume uses board lots; other equities use
shares. The **Volume** control in the Indicators dialog toggles the separate
histogram without hiding the numeric value. Rates have no traded volume, and
providers that report no volume retain their disclosed zero value.

## Timeframe policy

The new reference's timeframe list is a product target, not a claim that all
feeds publish every resolution. The current contract supports `1s`, `5s`,
`15s`, `30s`, `1m`, `3m`, `5m`, `10m`, `15m`, `30m`, `45m`, `1h`, `2h`, `4h`,
`6h`, `8h`, `12h`, `1d`, `1w`, and `1M`. `10s`, `3d`, `3M`, `6M`, `1Y`, tick
charts, and arbitrary custom periods are not yet contract resolutions. Each venue's
capabilities endpoint limits the chart menu to timeframes that its adapter
can actually serve.

| Source | Finest historical basis in this implementation | Intraday live basis | Long periods |
|---|---|---|---|
| Native crypto sources | Exchange REST candles at supported native resolutions | Binance and Bybit WebSocket subscriptions; OKX, Coinbase and Kraken REST polling, labelled POLL | Native candles or supported aggregation, per capability declaration |
| Deriv | Native WebSocket candle-history requests | Native WebSocket OHLC subscription | Native history or supported aggregation |
| Alpaca IEX | Historical IEX bars, plus bounded trades for seconds | IEX trades, quotes and bars | Exchange-calendar aggregation |
| Fugle free tier | 1m history from 2023-05-23 and daily history from 2010 | Per-symbol REST candles and quotes | 1m or daily observations aggregated on XTAI |
| U.S. Treasury | Published daily yields | None | Published daily observations aggregated on the USBOND calendar |
| FRED (optional key) | Published daily observations | None | Daily observations aggregated on USBOND; no intraday yields |

Daily observations cannot generate genuine intraday or seconds bars. A
real-time unfinished higher-period candle requires incoming prices from the
same source as its historical base. If an adapter has neither a historical
basis nor a live basis for a period, it must report that period as unavailable
instead of filling missing bars.

FRED is available with a free API key under distinct `FRED_*` rate IDs.
FRED is a daily source, not a substitute for live market prices. Fugle's free
account has limited WebSocket subscriptions; this integration stays within
the free REST allowance. Each active streaming chart owns its subscription and
releases it when the chart closes or changes instrument. The Dashboard shows
separate native providers and their actual transport; untouched REST feeds are
`IDLE`, and successful REST polling remains `POLL`. Legacy native adapters and
CCXT remain available for explicit compatibility configurations, outside the
default data-provider selection.

The execution engine retains Binance, Bitget, Hyperliquid, Lighter, MEXC,
Interactive Brokers, and SinoPac trading adapters. Cathay remains a declared
but unavailable broker adapter until an official trading API is implemented.
Changing `JOSEPH_ENABLED_VENUES` or `JOSEPH_TW_DATA_SOURCE` controls market
data only; it does not remove a trading adapter. The risk gateway still needs
valid market data for an order to proceed.

Provider references: [Alpaca market data](https://docs.alpaca.markets/us/docs/about-market-data-api),
[Fugle plans](https://developer.fugle.tw/pricing),
[FinMind API](https://api.finmindtrade.com/docs), and
[FRED observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html).

## Loading and live-price latency

The market engine warms provider instrument metadata concurrently after startup.
Interactive unfiltered search returns ready sources within one second while
slower sources continue in the background. Each source metadata load has a
twenty-second deadline, and failed sources retry independently. Authoritative
filter scans await their requested universe rather than silently omitting it.

Charts start history and WebSocket requests concurrently and retain newer live
prices when history arrives later. Alpaca subscribes to trades as well as bar
messages, so an unfinished candle can update before the next minute bar.
Trade-driven OHLCV previews are provisional; authoritative Alpaca bars replace
them. Regular-session charts ignore out-of-session trades and observe Alpaca's
sale-condition rules. Free IEX is one exchange, so quiet symbols and closed
market sessions can have no new trades even with a connected WebSocket.

A chart's first candle request starts together with the chart library, and
study runs (`/v1/scripts/run-batch`) wait up to 3 seconds while candles are
loading, so studies never queue ahead of the chart's own history (in the
browser and, through background priority, at the venue rate limits). The
terminal keeps the last twelve displayed series in memory: returning to one
draws it at once and revalidates it with a fresh batch.

The engine shares one venue fetch among identical concurrent history requests;
a client that disconnects does not cancel it for the others, and the result
still lands in the cache for its retry. A series whose newest bar was fetched
less than a twelfth of a bar ago (1 to 10 seconds) and has not closed since, or
that a live session keeps current, is served from the cache without another
venue request. Venue requests of studies, scans and strategies take a
rate-limit token only while no chart request waits for one. Alpaca reads the
pages of a long minute range concurrently within its rate limit.

TWSE and TPEx publish daily history one month per request, paced at one
request per 2 seconds. A daily chart request reads at most four uncached months
(about 6 seconds; weekly and monthly charts ten months, about 18 seconds) and
returns the newest bars it has; scrolling back (load older) reads the next
months, so history fills in progressively instead of a cold 500-bar daily
chart taking about 48 seconds. Months already read are reused, and concurrent
reads of one month share a request.

Chart history requests stop after 30 seconds and offer **Retry candles**.
Changing a chart's symbol, timeframe, or feed mode cancels its obsolete history
reads; older-history paging waits until the initial snapshot finishes.
WebSocket opening attempts retry after 10 seconds.

Inferred stock history windows stop at the exchange calendar's first supported
session. Monthly candles and indicator series therefore return the available
history when fewer than the requested number of bars exist. Indicator queries
also cap each lower-timeframe input at 10,000 candles
(`QueryConfig.max_intrabar_bars`). This prevents monthly studies from downloading
decades of hourly or minute data. Estimates outside that coverage remain null;
the engine does not invent delta values. True buy-volume delta remains available
where chart bars carry the necessary source data.

## Corporate-action price basis

Taiwan historical OHLC use Fugle's corporate-action adjustment at **every
supported timeframe**, including minutes, hours, days, weeks and months. Daily
observations come from confirmed `adjusted=true` responses. Fugle does not
provide adjusted minute candles, so the engine reads raw and adjusted daily
closes of the same symbol and session, divides adjusted by raw close, and applies
the ratio to that session's minute OHLC **before** aggregating any longer bar.
This is provider-evidenced normalization, without a hardcoded 0050 rule.

The policy covers forward/reverse splits, dividends and the capital changes
supported by Fugle. Its daily adjusted prices are rounded to two decimals;
derived intraday factors inherit that small precision limit. Volume, turnover,
trade counts and aggressor quantities remain as reported. Missing, mismatched
or unconfirmed daily evidence produces a data error rather than a guessed
factor or a silently raw segment. See [Fugle's price-adjustment documentation](https://developer.fugle.tw/docs/data/http-api/historical/candles/).

Alpaca REST bars use split adjustment at every supported native interval.
Historical trade-built seconds, recorded seconds loaded after a restart, and
historical closing-print prices now use ratios from paired raw/split daily
bars on the same feed. This prevents raw closing prints from producing a spike
inside an adjusted minute or hour bar. Alpaca split adjustment also rescales
historical shares by the inverse price factor: seconds, archived constituents
and closing prints match the REST bar volume convention. Public tape endpoints,
current-session quotes, trades and order prices remain native. Live
weekly/monthly aggregates refresh their constituent days on every adjustment
epoch; cached closing prints stay native and are normalized afresh when folded.
Exchange-local dates distinguish live prices from yesterday's history even
before the next open and on weekends. See
[Alpaca's historical-bar adjustment policy](https://docs.alpaca.markets/us/reference/stockbarsingle-1).

`JOSEPH_FUGLE_ADJUSTED_DAILY=true` retains its legacy name but now controls all
Taiwan historical resolutions. Set it to `false` and restart the market engine
only when explicitly requesting raw historical prices with their action gaps.

`CandleBatch.price_adjustment` identifies `RAW`, `SPLIT` or `ALL`; adjusted
batches also declare a price-basis epoch (`price_adjustment_epoch_ms`) and a
refresh deadline (`price_adjustment_refresh_after_ms`, the next fifteen-minute
UTC boundary). Once per fifteen-minute epoch, on the first request for an
instrument, the engine re-reads the provider's recent daily factors (paired
raw/split daily bars for Alpaca, raw/adjusted daily closes for Fugle: one
request pair covering the last 30 days) and compares them with the previous
reading. A new split, dividend or provider correction changes the factor of
every session before its ex-date, so it shows in those recent sessions; only
then are the instrument's adjusted cached series dropped and the basis epoch
advanced. Unchanged factors keep the cached history and the epoch, so charts
and indicators no longer reload all adjusted history four times an hour. If
the factors cannot be read, the cache is dropped as before. The first use of an
instrument reads its baseline in the background, without delaying the chart.
Sources without this check (none of the adjusted ones today) keep the old
per-epoch invalidation. The chart discards incompatible history pages when the
basis epoch changes. Provider history uses the latest known corporate actions
and can be revised; it is not point-in-time data and does not reconstruct
historically known adjustments for backtests. No split factors are inferred
from apparent price jumps.

Open adjusted charts revalidate automatically after the server-provided
deadline; with an unchanged basis this is a cached read. Changing the basis
epoch replaces earlier pages rather than splicing newly normalized prices into
old history. Factor loads that cross an epoch boundary are retried within the
new epoch instead of failing the history request. Ordinary session gaps remain; missing
closed bars use the existing bounded source-repair path. An unconfirmed provider
adjustment fails visibly. Genuine market jumps and missing observations are
retained; apparent discontinuities alone never trigger smoothing.
