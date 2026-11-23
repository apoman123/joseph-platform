/**
 * @fileoverview Display formatting helpers (display only; never used to
 * compute order parameters).
 */
import {
  Decimal,
  TIMEFRAME_MS,
  isCalendarTimeframe,
  isSecondsTimeframe,
  stepDecimals,
  timeframeEnd,
  zonedParts,
  type BarCalendar,
  type Instrument,
  type TickStep,
  type Timeframe,
} from '@joseph/contracts';
import {zoneAbbreviation} from '@joseph/chart-core';

/**
 * Formats a decimal string with grouping and fixed digits.
 * @param value Decimal string or null.
 * @param digits Fraction digits.
 * @return Text or an em dash for missing values.
 */
export function formatDecimal(
  value: string | null | undefined,
  digits = 2
): string {
  if (value == null || !Decimal.isValid(value)) return '—';
  const fixed = Decimal.parse(value).toFixed(digits);
  const [whole = '0', fraction] = fixed.split('.');
  const negative = whole.startsWith('-');
  const grouped = (negative ? whole.slice(1) : whole).replace(
    /\B(?=(\d{3})+(?!\d))/g,
    ','
  );
  return `${negative ? '−' : ''}${grouped}${fraction ? `.${fraction}` : ''}`;
}

/**
 * Formats a price using the instrument tick precision.
 * @param value Price.
 * @param tickSize Tick size.
 * @return Text.
 */
export function formatPrice(
  value: string | null | undefined,
  tickSize: string | null
): string {
  return formatDecimal(
    value,
    tickSize ? Math.min(8, stepDecimals(tickSize)) : 2
  );
}

/**
 * Formats a number for display.
 * @param value Number.
 * @param digits Fraction digits.
 * @return Text.
 */
export function formatNumber(
  value: number | null | undefined,
  digits = 2
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return value
    .toLocaleString('en-US', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    })
    .replace('-', '−');
}

/**
 * Formats large USD amounts compactly (e.g. `$5.75B`).
 * @param value Decimal string.
 * @return Text.
 */
export function formatUsdCompact(value: string | null | undefined): string {
  if (value == null || !Decimal.isValid(value)) return '—';
  const n = Number(value);
  const units: [number, string][] = [
    [1e12, 'T'],
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ];
  for (const [size, suffix] of units) {
    if (Math.abs(n) >= size) return `$${(n / size).toFixed(2)}${suffix}`;
  }
  return `$${n.toFixed(2)}`;
}

/**
 * Formats a signed percent.
 * @param value Decimal string percent.
 * @param digits Digits.
 * @return Text like `+1.25%`.
 */
export function formatPct(
  value: string | null | undefined,
  digits = 2
): string {
  if (value == null || !Decimal.isValid(value)) return '—';
  const fixed = Decimal.parse(value).toFixed(digits);
  return `${fixed.startsWith('-') ? '−' : '+'}${fixed.replace('-', '')}%`;
}

/**
 * Formats a UTC time of day.
 * @param ms UTC ms.
 * @param seconds Include seconds.
 * @return `HH:MM[:SS] UTC`.
 */
export function formatUtcTime(
  ms: number | null | undefined,
  seconds = false
): string {
  if (ms == null) return '—';
  return `${new Date(ms).toISOString().slice(11, seconds ? 19 : 16)} UTC`;
}

/**
 * Formats an age in compact units.
 * @param ms Age in ms.
 * @return Text like `42 s` or `3 min`.
 */
export function formatAge(ms: number): string {
  if (ms < 1000) return 'now';
  if (ms < 60_000) return `${Math.round(ms / 1000)} s`;
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)} min`;
  return `${Math.round(ms / 3_600_000)} h`;
}

/** Month names used in bar labels. */
const MONTH_NAMES = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/**
 * ISO-like text of an instant in a time zone (`YYYY-MM-DDTHH:MM:SS`).
 * @param ms UTC ms.
 * @param timeZone IANA time zone.
 * @return Local text (no zone suffix).
 */
function zonedIso(ms: number, timeZone: string): string {
  const p = zonedParts(ms, timeZone);
  return new Date(
    Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second)
  ).toISOString();
}

/**
 * Exchange time zone of a calendar, or null for `24x7` (UTC labels).
 * @param calendar Session calendar.
 * @return Time zone or null.
 */
function exchangeZone(calendar: BarCalendar | null | undefined): string | null {
  return calendar && !calendar.continuous ? calendar.timeZone : null;
}

/**
 * Formats a bar start for its timeframe: seconds for sub-minute bars, the
 * date only from daily bars and the month name for `1M`. Exchange
 * calendars show exchange time (`ET`, `TPE`).
 * @param timeframe Chart timeframe.
 * @param startMs Bar start (UTC ms).
 * @param calendar Session calendar (default UTC).
 * @return Text such as `2026-11-22 14:03:45 UTC` or `Feb 2026`.
 */
export function formatBarTime(
  timeframe: Timeframe,
  startMs: number,
  calendar?: BarCalendar | null
): string {
  const zone = exchangeZone(calendar);
  if (zone) {
    const local = zonedIso(startMs, zone);
    const suffix = zoneAbbreviation(zone);
    if (isCalendarTimeframe(timeframe)) {
      const month = MONTH_NAMES[Number(local.slice(5, 7)) - 1] ?? '';
      return `${month} ${local.slice(0, 4)}`;
    }
    if (isSecondsTimeframe(timeframe))
      return `${local.slice(0, 19).replace('T', ' ')} ${suffix}`;
    if (TIMEFRAME_MS[timeframe] >= TIMEFRAME_MS['1d'])
      return local.slice(0, 10);
    return `${local.slice(0, 16).replace('T', ' ')} ${suffix}`;
  }
  const iso = new Date(startMs).toISOString();
  if (isCalendarTimeframe(timeframe)) {
    const date = new Date(startMs);
    return `${MONTH_NAMES[date.getUTCMonth()] ?? ''} ${date.getUTCFullYear()}`;
  }
  if (isSecondsTimeframe(timeframe)) {
    return `${iso.slice(0, 19).replace('T', ' ')} UTC`;
  }
  if (TIMEFRAME_MS[timeframe] >= TIMEFRAME_MS['1d']) {
    return `${iso.slice(0, 10)} UTC`;
  }
  return `${iso.slice(0, 16).replace('T', ' ')} UTC`;
}

/**
 * Formats when a bar closes (its exclusive end, calendar-aware: monthly
 * bars close on the 1st of the next month; session bars at the calendar's
 * bar end, e.g. the 13:00 early close, in exchange time).
 * @param timeframe Chart timeframe.
 * @param startMs Bar start (UTC ms).
 * @param calendar Session calendar (default UTC).
 * @return Text such as `14:05:00 UTC`, `2026-03-01 00:00 UTC` or
 *     `2026-11-27 13:00 ET`.
 */
export function formatBarClose(
  timeframe: Timeframe,
  startMs: number,
  calendar?: BarCalendar | null
): string {
  const zone = exchangeZone(calendar);
  if (zone && calendar) {
    const local = zonedIso(calendar.endOf(timeframe, startMs), zone);
    const suffix = zoneAbbreviation(zone);
    if (TIMEFRAME_MS[timeframe] >= TIMEFRAME_MS['1d'])
      return `${local.slice(0, 16).replace('T', ' ')} ${suffix}`;
    return `${local.slice(11, 19)} ${suffix}`;
  }
  const end = timeframeEnd(timeframe, startMs);
  const iso = new Date(end).toISOString();
  if (TIMEFRAME_MS[timeframe] >= TIMEFRAME_MS['1d']) {
    return `${iso.slice(0, 16).replace('T', ' ')} UTC`;
  }
  return `${iso.slice(11, 19)} UTC`;
}

/**
 * Formats a non-negative amount compactly (`950`, `12.4K`, `1.2M`, `3.4B`).
 * @param value Decimal string or number.
 * @return Text, or an em dash when missing.
 */
export function formatCompact(
  value: string | number | null | undefined
): string {
  const n = typeof value === 'number' ? value : Number(value ?? NaN);
  if (value == null || value === '' || !Number.isFinite(n)) return '—';
  const units: [number, string][] = [
    [1e12, 'T'],
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ];
  // Round first, so 999,960 reads `1.0M` rather than `1000K`.
  for (const [size, suffix] of units) {
    const scaled = n / size;
    const digits = Math.abs(scaled) >= 99.95 ? 0 : 1;
    if (Math.abs(Number(scaled.toFixed(digits))) >= 1) {
      return `${scaled.toFixed(digits)}${suffix}`;
    }
  }
  return n >= 100 ? n.toFixed(0) : n.toFixed(n >= 10 ? 1 : 2);
}

/** Instrument fields used for price and volume display. */
export type DisplayInstrument = Pick<
  Instrument,
  'tick_size' | 'instrument_id'
> &
  Partial<Pick<Instrument, 'tick_ladder' | 'board_lot' | 'calendar' | 'key'>>;

/**
 * Tick size at a price (TWSE-style ladders), else the instrument tick.
 * @param tickSize Instrument tick size.
 * @param ladder Price-dependent ticks (may be empty).
 * @param price Price.
 * @return Tick size text.
 */
export function tickAt(
  tickSize: string,
  ladder: readonly TickStep[] | null | undefined,
  price: number
): string {
  let tick = tickSize;
  const sorted = [...(ladder ?? [])].sort(
    (a, b) => Number(a.min_price) - Number(b.min_price)
  );
  for (const step of sorted) {
    if (price >= Number(step.min_price)) tick = step.tick_size;
  }
  return tick;
}

/**
 * Tick that gives the price axis enough precision for a price range: the
 * finest ladder tick between `low` and `high` (the instrument tick without
 * a ladder).
 * @param instrument Instrument.
 * @param low Lowest price in view.
 * @param high Highest price in view.
 * @return Tick size text.
 */
export function displayTickSize(
  instrument: DisplayInstrument,
  low: number,
  high: number
): string {
  const ladder = instrument.tick_ladder ?? [];
  if (ladder.length === 0 || !Number.isFinite(low) || !Number.isFinite(high))
    return instrument.tick_size;
  let finest = tickAt(instrument.tick_size, ladder, high);
  for (const step of ladder) {
    const min = Number(step.min_price);
    if (min > high) continue;
    const tick = tickAt(instrument.tick_size, ladder, Math.max(min, low));
    if (stepDecimals(tick) > stepDecimals(finest)) finest = tick;
  }
  return finest;
}

/**
 * Formats a price with the precision of its ladder rung; yields get `%`.
 * @param value Price (decimal string or number).
 * @param instrument Instrument, or null while loading (2 decimals).
 * @return Text such as `585`, `48.55` or `4.253%`.
 */
export function formatInstrumentPrice(
  value: string | number | null | undefined,
  instrument: DisplayInstrument | null
): string {
  if (value == null || value === '') return '—';
  const text = typeof value === 'number' ? String(value) : value;
  if (!instrument) return formatPrice(text, null);
  const tick = tickAt(
    instrument.tick_size,
    instrument.tick_ladder,
    Number(text)
  );
  const formatted = formatPrice(text, tick);
  return instrument.key?.market_type === 'rate' && formatted !== '—'
    ? `${formatted}%`
    : formatted;
}

/**
 * Shares per board lot when volumes are shown in lots (TWSE/TPEx 張).
 * @param instrument Instrument, or null while loading.
 * @return Board lot, or null when volumes are shown in shares or units.
 */
export function lotSizeForVolume(
  instrument: DisplayInstrument | null
): number | null {
  if (!instrument?.board_lot || instrument.calendar !== 'XTAI') return null;
  const lot = Number(instrument.board_lot);
  return Number.isFinite(lot) && lot > 1 ? lot : null;
}

/**
 * Formats a bar volume in the market's usual unit: TWSE and TPEx in board
 * lots (張, 1 張 = 1,000 shares), other securities in shares, crypto in
 * base units.
 * @param volume Base volume (shares or base asset).
 * @param instrument Instrument, or null while loading.
 * @return Text such as `1,234 張`, `52,300 sh` or `12.50`.
 */
export function formatVolume(
  volume: number | null | undefined,
  instrument: DisplayInstrument | null
): string {
  if (volume == null || !Number.isFinite(volume)) return '—';
  const lot = lotSizeForVolume(instrument);
  if (lot) return `${formatNumber(volume / lot, 0)} 張`;
  const type = instrument?.key?.market_type;
  if (type === 'stock' || type === 'etf')
    return `${formatNumber(volume, 0)} sh`;
  return formatNumber(volume, 2);
}
