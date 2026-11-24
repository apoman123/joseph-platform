/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Display formatting for prices, quantities and percentages.
 *
 * Prices and quantities are formatted from decimal strings with `Decimal`, so
 * the displayed value matches exactly what an intent would carry; they never
 * pass through `number`. Only timestamps and ages are numbers.
 */

import {Decimal, stepDecimals} from '@joseph/contracts';

/**
 * Inserts thousands separators into the integer part of a fixed string.
 * @param text Plain decimal text such as `-95000.5`.
 * @return Grouped text such as `-95,000.5`.
 */
export function groupThousands(text: string): string {
  const negative = text.startsWith('-');
  const body = negative ? text.slice(1) : text;
  const [whole = '0', fraction] = body.split('.');
  // Matches each position inside the integer part that is followed by a
  // multiple of three digits up to its end.
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${negative ? '-' : ''}${grouped}${fraction === undefined ? '' : `.${fraction}`}`;
}

/**
 * Formats a decimal string with the precision implied by a step size.
 * @param value Decimal string, or null or undefined when missing.
 * @param step Tick or lot size, e.g. `0.1`.
 * @param fallback Text for missing or invalid values.
 * @return Grouped fixed-point text, e.g. `95,000.0`, rounded half-even.
 */
export function formatStep(
  value: string | null | undefined,
  step: string,
  fallback = '—'
): string {
  if (value == null || !Decimal.isValid(value)) return fallback;
  return groupThousands(Decimal.parse(value).toFixed(stepDecimals(step)));
}

/**
 * Formats a decimal string with a fixed number of fraction digits.
 * @param value Decimal string, or null or undefined when missing.
 * @param digits Fraction digits.
 * @param fallback Text for missing or invalid values.
 * @return Grouped fixed-point text, rounded half-even.
 */
export function formatFixed(
  value: string | null | undefined,
  digits: number,
  fallback = '—'
): string {
  if (value == null || !Decimal.isValid(value)) return fallback;
  return groupThousands(Decimal.parse(value).toFixed(digits));
}

/**
 * Formats a percentage decimal string with an explicit sign.
 * @param value Percent value as a decimal string (e.g. `-1.234`), or null or
 *     undefined when missing.
 * @param digits Fraction digits.
 * @return Text such as `+1.23%`, or `—` for missing or invalid values.
 */
export function formatSignedPct(
  value: string | null | undefined,
  digits = 2
): string {
  if (value == null || !Decimal.isValid(value)) return '—';
  const parsed = Decimal.parse(value);
  const text = parsed.toFixed(digits);
  return `${parsed.isNegative() || text.startsWith('-') ? '' : '+'}${text}%`;
}

/**
 * Formats a compact USD amount, e.g. `$52.3M`.
 * @param value Decimal string, or null or undefined when missing.
 * @return Text such as `$52.3M` (one decimal with a `K`, `M` or `B`
 *     suffix), whole dollars below $1,000, or `—` for missing or invalid
 *     values.
 */
export function formatCompactUsd(value: string | null | undefined): string {
  if (value == null || !Decimal.isValid(value)) return '—';
  const amount = Decimal.parse(value);
  const units: ReadonlyArray<[string, string]> = [
    ['1000000000', 'B'],
    ['1000000', 'M'],
    ['1000', 'K'],
  ];
  for (const [size, suffix] of units) {
    const threshold = Decimal.parse(size);
    if (amount.abs().cmp(threshold) >= 0) {
      return `$${amount.div(threshold).toFixed(1)}${suffix}`;
    }
  }
  return `$${amount.toFixed(0)}`;
}

/**
 * Formats a UTC timestamp as `HH:MM:SS UTC`.
 * @param ms UTC epoch milliseconds.
 * @return Time text.
 */
export function formatUtcTime(ms: number): string {
  return `${new Date(ms).toISOString().slice(11, 19)} UTC`;
}

/**
 * Formats a UTC timestamp as `YYYY-MM-DD HH:MM UTC`.
 * @param ms UTC epoch milliseconds.
 * @return Date-time text.
 */
export function formatUtcDateTime(ms: number): string {
  return `${new Date(ms).toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

/**
 * Formats a data age in milliseconds as a short human string.
 * @param ageMs Age in milliseconds.
 * @return Text such as `4s`, `3m` or `2h`.
 */
export function formatAge(ageMs: number): string {
  const seconds = Math.max(0, Math.round(ageMs / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.round(minutes / 60)}h`;
}
