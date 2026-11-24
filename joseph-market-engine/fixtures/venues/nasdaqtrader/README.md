# Nasdaq Trader symbol directory fixtures

Captured live on 2026-11-23 by
`scripts/capture_venue_fixtures.py nasdaqtrader` from
`https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt` and
`otherlisted.txt` (header, a selection of rows and the `File Creation Time`
footer kept verbatim). The rows cover Nasdaq stocks and ETFs, NYSE /
NYSE Arca listings, class shares (`BRK.B`), bond ETFs, Cboe BZX listings
(bond ETFs `GOVT` and `BBAG`, the gold ETF `AAAU`, the stock `CBOE` and the
ETN `VXX`), a TXSE listing (`CECO`), a preferred (`ABR$D`), units, warrants
and rights, and test issues on several exchanges (`ZXYZ.A`, `ZEXIT`,
`MTEST`, `ZZZTX`, `ZBZX`). The Cboe BZX, TXSE and `ZBZX` rows were added
by a recapture on 2026-11-23 (`File Creation Time` 1123202603:02).
