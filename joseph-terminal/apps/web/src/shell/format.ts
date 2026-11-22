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
  type Timeframe,
} from '@joseph/contracts';

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
 * Formats a bar start for its timeframe: seconds for sub-minute bars, the
 * date only from daily bars and the month name for `1M`.
 * @param timeframe Chart timeframe.
 * @param startMs Bar start (UTC ms).
 * @return Text such as `2026-11-22 14:03:45 UTC` or `Feb 2026`.
 */
export function formatBarTime(timeframe: Timeframe, startMs: number): string {
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
 * bars close on the 1st of the next month).
 * @param timeframe Chart timeframe.
 * @param startMs Bar start (UTC ms).
 * @return Text such as `14:05:00 UTC` or `2026-03-01 00:00 UTC`.
 */
export function formatBarClose(timeframe: Timeframe, startMs: number): string {
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
