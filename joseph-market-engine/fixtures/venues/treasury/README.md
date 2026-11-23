# Treasury fixtures

Captured live on 2026-11-23 by `scripts/capture_venue_fixtures.py treasury`
from the per-year CSV export of
`https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{year}/all?type=...&field_tdr_date_value={year}&page&_format=csv`
(unmodified bodies).

| File | Why |
|---|---|
| `nominal_2026.csv` | Current year, all 14 nominal tenors including `1.5 Month` and `4 Mo` |
| `nominal_2002.csv` | `1 Mo` and `20 Yr` present; `30 Yr` blank from 2002-02-19 |
| `nominal_1990.csv` | First year: no `1 Mo`, `2 Mo`, `4 Mo` or `20 Yr` columns |
| `real_2026.csv` | Par real yields (`5 YR` ... `30 YR`) |
