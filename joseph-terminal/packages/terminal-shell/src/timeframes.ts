/**
 * @fileoverview Custom timeframe input: parses what users type (`15`,
 * `4H`, `1D`, `90s`) and maps it to a timeframe the market engine serves.
 * Resolutions the engine does not offer are reported, never approximated.
 */
import {TIMEFRAMES, TIMEFRAME_MS, type Timeframe} from '@joseph/contracts';

/** Parse result of a custom timeframe. */
export type TimeframeParse =
  {ok: true; timeframe: Timeframe} | {ok: false; error: string};

/** Unit suffixes in milliseconds. */
const UNITS: Readonly<Record<string, number>> = {
  s: 1_000,
  m: 60_000,
  h: 3_600_000,
  d: 86_400_000,
  w: 604_800_000,
};

/**
 * Parses a typed resolution. A bare number means minutes, as on most
 * charting tools; `M` (uppercase) is months.
 * @param input Text such as `15`, `15m`, `4h`, `1D`, `1W`.
 * @return Supported timeframe or an actionable error.
 */
export function parseTimeframeInput(input: string): TimeframeParse {
  const text = input.trim();
  const match = /^(\d{1,4})?\s*([smhdwM]|min|mins|hr|hrs)?$/i.exec(text);
  if (!text || !match) {
    return {ok: false, error: 'Type a resolution such as 15, 4h or 1D.'};
  }
  const count = Number(match[1] ?? '1');
  const rawUnit = match[2] ?? 'm';
  if (rawUnit === 'M') {
    return {
      ok: false,
      error: 'Monthly bars are not offered by the market engine yet.',
    };
  }
  const unit = rawUnit.toLowerCase().startsWith('h')
    ? 'h'
    : rawUnit.toLowerCase().startsWith('min')
      ? 'm'
      : rawUnit.toLowerCase();
  const unitMs = UNITS[unit];
  if (!unitMs || count <= 0) {
    return {ok: false, error: 'Type a resolution such as 15, 4h or 1D.'};
  }
  const durationMs = count * unitMs;
  const timeframe = TIMEFRAMES.find(tf => TIMEFRAME_MS[tf] === durationMs);
  if (timeframe) return {ok: true, timeframe};
  return {
    ok: false,
    error: `${count}${unit} bars are not offered. Available: ${TIMEFRAMES.join(', ')}.`,
  };
}
