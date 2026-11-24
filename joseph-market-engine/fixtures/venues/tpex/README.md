# TPEx fixtures

Contract-test inputs for the TPEx adapter, recorded from the official free
TPEx and MIS endpoints. Captured live on 2026-11-23 at 16:17 Asia/Taipei (a Monday, after the
09:00-13:30 session and after TPEx refreshed its OpenAPI quotes to that
session) by `scripts/capture_venue_fixtures.py tpex`, trimmed to a few
securities. No credentials are involved.

| File | Source |
|---|---|
| `mainboard_quotes.json` | `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes` (上櫃股票收盤行情, session 2026-11-23: board-lot values and next-session limit prices; stocks in every tick band, a new listing, domestic, bond and active-bond ETFs, and the excluded foreign-currency counter `00687C`, preferred share `8349A` and ETN `020025`) |
| `t187ap03_O.json` | `.../openapi/v1/mopsfin_t187ap03_O` (上櫃股票基本資料; `DateOfListing` is Gregorian `20150925`, `Symbol` is padded with full-width spaces; fields trimmed; `4804` and `8084` are suspended and absent from the quotes) |
| `ipo_no_limit.json` | `.../openapi/v1/tpex_ipo_no_limit` (first five trading days of `7848`, 2026-11-16 to 11-20) |
| `cmode.json` | `.../openapi/v1/tpex_cmode` (altered/periodic trading and suspensions, full-width `Ｙ` flags; trimmed) |
| `trading_date_2026.json`, `trading_date_2027.json` | `https://www.tpex.org.tw/www/zh-tw/bulletin/tradingDate?date=YYYY&response=json` (開休市日期表 as an HTML table) |
| `trading_stock_6488.json`, `trading_stock_00679B.json` | `https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=...&date=2026/11/01&response=json` (volume in 張 = thousand shares, turnover in thousand TWD) |
| `trading_stock_00679B_201701.json` | The same for `date=2017/01/01`: the listing day is `106/01/17*` and the volume field is `成交仟股` |
| `mis_series.json` | 3 polls (5 s apart) of `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=otc_6488.tw\|...\|otc_2330.tw&json=1&delay=0` after the close: data time `tlong` 14:30, trade time 13:30, `z` the closing price, `u` 9999.95 and no `w` for unlimited ETFs; the second response came from the server cache; `otc_2330` (a TWSE listing) returns MIS's empty record |

No in-session MIS recording of TPEx was captured (the capture ran after
the close); intraday recording is the code path TWSE's in-session fixtures
(`../twse/mis_recorded.json`) test.
