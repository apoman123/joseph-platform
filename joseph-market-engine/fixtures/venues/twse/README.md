# TWSE fixtures

Contract-test inputs for the TWSE adapter, recorded from TWSE's official
free endpoints. Captured live on 2026-11-23 (a Monday, during the 09:00-13:30 Asia/Taipei
session) by `scripts/capture_venue_fixtures.py twse`, trimmed to a few
securities. No credentials are involved.

| File | Source |
|---|---|
| `stock_day_all.json` | `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` (last session 2026-11-20; stocks, ETFs and excluded REIT/preferred/foreign-currency rows) |
| `t187ap03_L.json` | `.../v1/opendata/t187ap03_L` (listed companies; `上市日期` is Gregorian `19940905`; fields trimmed) |
| `t187ap47_L.json` | `.../v1/opendata/t187ap47_L` (listed funds; `上市日期` is ROC `0920630`; fields trimmed) |
| `twt88u.json`, `twtawu.json` | `.../v1/exchangeReport/TWT88U` (first five trading days) and `TWTAWU` (suspensions) |
| `holiday_openapi.json` | `.../v1/holidaySchedule/holidaySchedule` (it already serves the 2027 schedule) |
| `holiday_rwd_2026.json`, `holiday_rwd_2027.json` | `https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json&date=YYYY0101` |
| `stock_day_2330.json`, `stock_day_0050.json` | `https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=20261101&stockNo=...&response=json` |
| `mis_series.json` | 24 polls (one per 5 s) of `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_2330.tw\|...\|tse_00679B.tw&json=1&delay=0` around 12:28-12:30; 21 of 24 responses came from the server cache (`exKey`, `cachedAlive`); `00679B` (a TPEx listing) returns MIS's empty record |
| `mis_recorded.json` | Distinct responses of a 10-minute recording (11:50-12:00, one poll per 5 s, 109 of 120 cached), trimmed to 0050, 1101 and 2330 |
| `mis_close.json` | Closing call (13:26, 13:27: trial price `pz` and volume `ps`, `v` unchanged) and the 13:33 snapshot whose data time is 13:30:00 with `z` = closing price and `tv` = the auction volume in board lots (2330: 5,897 = the jump in `v`); trimmed to 2330 and 0050 |
