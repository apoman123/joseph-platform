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
| Crypto perpetuals | Free CCXT Pro for Binance, Bitget, Hyperliquid, Lighter, MEXC | Public WebSocket candles (Lighter uses trades), REST history, tickers and books; exchange-listed RWA contracts are included. |
| US stocks and ETFs | Alpaca IEX | Free Alpaca key required; IEX is one exchange, not consolidated SIP. |
| Taiwan stocks and ETFs | Fugle free-tier per-symbol REST | Fugle key required for 1m history from 2023-05-23 and current-session REST candles/quotes. Taiwan market data stays disabled without the key. |
| US Treasury rates | U.S. Treasury daily par and real yields; optional FRED daily rates and spreads | FRED key required for distinct `FRED_*` IDs. Published daily; not a live bond quote or tradable price. |
| Demo only | Mock venue | Synthetic data only when `JOSEPH_DEMO_ONLY=true`; excluded from live market-data venues. |

SinoPac Shioaji is no longer selectable or constructed as a market-data source
by the market engine. The market engine no longer installs its SDK extra or
reads its data-feed credentials. Its trading adapter remains in the execution
engine.

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
| CCXT crypto venues | Exchange REST candles at supported native resolutions | CCXT Pro WebSocket subscriptions for active charts; REST fallback is labelled POLL | CCXT candles or supported aggregation, per capability declaration |
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
the free REST allowance. The installed CCXT package includes free CCXT Pro
WebSocket support. Each active chart owns its subscription and releases it
when the chart closes or changes instrument. Lighter's first live bucket has
partial volume until its authoritative REST reconciliation at bar close. The Dashboard displays one CCXT
provider for the five crypto venues, and marks untouched REST feeds `IDLE`.

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
The first cold request can still wait for its exchange, particularly Hyperliquid
HIP-3 metadata. Warmup keeps this work out of later symbol changes. Concurrent
identical CCXT history requests share one network download; a cancelled caller
does not cancel another chart's download.

Charts start history and WebSocket requests concurrently and retain newer live
prices when history arrives later. Alpaca subscribes to trades as well as bar
messages, so an unfinished candle can update before the next minute bar.
Trade-driven OHLCV previews are provisional; authoritative Alpaca bars replace
them. Regular-session charts ignore out-of-session trades and observe Alpaca's
sale-condition rules. Free IEX is one exchange, so quiet symbols and closed
market sessions can have no new trades even with a connected WebSocket.

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
