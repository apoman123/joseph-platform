/**
 * @fileoverview Display formatting helpers (display only; never used to
 * compute order parameters).
 */
import {Decimal, stepDecimals} from '@joseph/contracts';

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
