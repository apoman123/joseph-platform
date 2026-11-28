# Alpaca market-data fixtures

Contract-test inputs for the Alpaca adapter (Nasdaq, NYSE and Cboe BZX
data). These files are **hand-written** from Alpaca's documentation, not
captured: they have exactly the field names, nesting and types of the
documented responses, with illustrative values (AAPL, 2026-11-20, a regular
XNYS session starting 14:30 UTC). They contain no credentials.

| File | Endpoint / message | Documentation |
|---|---|---|
| `bars_1min_page1.json`, `bars_1min_page2.json` | `GET /v2/stocks/{symbol}/bars?timeframe=1Min` (two pages joined by `next_page_token`; the first bar is pre-market) | https://docs.alpaca.markets/reference/stockbarsingle-1 |
| `bars_1day.json` | `GET /v2/stocks/{symbol}/bars?timeframe=1Day` (bars stamped at New York midnight) | https://docs.alpaca.markets/reference/stockbarsingle-1 |
| `trades.json` | `GET /v2/stocks/{symbol}/trades` (`c` conditions, `u` update flag, tape `z`) | https://docs.alpaca.markets/reference/stocktradesingle-1 |
| `quotes.json` | `GET /v2/stocks/{symbol}/quotes` (sizes in shares since 2025-11-03) | https://docs.alpaca.markets/reference/stockquotesingle-1 |
| `snapshots.json` | `GET /v2/stocks/snapshots?symbols=...` (`latestTrade`, `latestQuote`, `minuteBar`, `dailyBar`, `prevDailyBar`) for AAPL, QQQ, TLT, BRK.B, SPY and GOVT (Cboe BZX, tape B) | https://docs.alpaca.markets/reference/stocksnapshots-1 |
| `ws_frames.json` | `wss://stream.data.alpaca.markets/v2/{feed}`: `success` (connected, authenticated), `subscription`, then `q`, `t`, `b`, `u`, `d` and `s` messages | https://docs.alpaca.markets/docs/real-time-stock-pricing-data, https://docs.alpaca.markets/docs/streaming-market-data |
| `ws_errors.json` | stream `error` messages 402 (auth failed) and 406 (connection limit exceeded) | https://docs.alpaca.markets/docs/streaming-market-data |
| `close_bars_1min.json` | `GET /v2/stocks/AAPL/bars?timeframe=1Min` around the 2026-11-20 close (15:57-16:01 New York plus 13:30 and 15:30): the 16:00 minute holds the closing cross and post-market trades | https://docs.alpaca.markets/reference/stockbarsingle-1 |
| `close_trades.json` | `GET /v2/stocks/AAPL/trades` for the closing window `[16:00, 16:01)`: the Nasdaq closing cross (`6`), another market center's closing print, an odd-lot closing print (`6`, `I`), a canceled print (`u`), Form T trades (`T`) | https://docs.alpaca.markets/reference/stocktradesingle-1 |
| `close_bars_1day.json` | `GET /v2/stocks/AAPL/bars?timeframe=1Day` for 2026-11-19/20; the 2026-11-20 close is the closing cross price (daily bars: `6` updates the close, `T` does not) | https://docs.alpaca.markets/reference/stockbarsingle-1 |
| `bars_multi_page1.json`, `bars_multi_page2.json` | `GET /v2/stocks/bars?symbols=AAPL,SPY&timeframe=1Day` (multi-symbol daily bars keyed by symbol, two pages joined by `next_page_token`; used by heatmaps) | https://docs.alpaca.markets/reference/stockbars-1 |
| `early_close_bars_1min.json`, `early_close_trades.json` | the same two endpoints for GOVT (Cboe BZX, tape B) around the 13:00 early close of 2025-11-28 | https://docs.alpaca.markets/reference/stocktradesingle-1 |

Sale-condition update rules used for bars:
https://docs.alpaca.markets/docs/market-data-faq ("How are bars aggregated?";
minute and daily bars differ: `T` updates a minute bar's open/close but only
a daily bar's volume, `6` updates both). The trades endpoint's `start` and
`end` are inclusive.
Plans, rate limits (200 requests/min) and stream limits (1 connection, 30
symbols on the free plan): https://docs.alpaca.markets/docs/about-market-data-api.

To replace them with real responses, record the same endpoints with an Alpaca
key. `tests/network/test_live_exchanges.py` checks the live API when
`JOSEPH_NETWORK_TESTS=1` and the keys are set.
