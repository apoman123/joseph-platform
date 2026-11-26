/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for TradingView-style timeframe input (seconds and
 * calendar months), venue availability, menu groups, the bar-close
 * countdown and calendar-aware replay point-in-time.
 */

import {TIMEFRAMES, type VenueCapabilities} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  chooseReplayStart,
  replayAsOfMs,
  startReplay,
  stepReplay,
} from './replay';
import {
  TIMEFRAME_GROUPS,
  formatCountdown,
  msUntilBarClose,
  offeredTimeframes,
  parseTimeframeInput,
  timeframeAvailability,
  timeframeTitle,
} from './timeframes';

const BINANCE: Pick<
  VenueCapabilities,
  'display_name' | 'timeframes' | 'trade_built_timeframes'
> = {
  display_name: 'Binance USDⓈ-M',
  timeframes: ['1m', '5m', '1h', '1d', '1w', '1M'],
  trade_built_timeframes: ['1s', '5s'],
};

describe('typed resolutions', () => {
  it.each([
    ['1S', '1s'],
    ['5S', '5s'],
    ['15S', '15s'],
    ['30S', '30s'],
    ['1s', '1s'],
    ['S', '1s'],
    ['60S', '1m'],
    ['1M', '1M'],
    ['M', '1M'],
    ['1 month', '1M'],
    ['1m', '1m'],
    ['1min', '1m'],
    ['240', '4h'],
    ['10', '10m'],
    ['45m', '45m'],
    ['8h', '8h'],
    ['1440', '1d'],
    ['7D', '1w'],
  ])('maps %s to %s', (input, expected) => {
    expect(parseTimeframeInput(input)).toEqual({
      ok: true,
      timeframe: expected,
    });
  });

  it('never treats a fixed 31-day span as a calendar month', () => {
    const parsed = parseTimeframeInput('31D');
    expect(parsed.ok).toBe(false);
  });

  it('refuses timeframes the venue does not offer', () => {
    const offered = offeredTimeframes(BINANCE);
    expect(parseTimeframeInput('5S', offered)).toEqual({
      ok: true,
      timeframe: '5s',
    });
    const refused = parseTimeframeInput('15S', offered);
    expect(refused.ok).toBe(false);
    if (!refused.ok) expect(refused.error).toMatch(/not offered by this venue/);
    expect(parseTimeframeInput('15S', null).ok).toBe(true);
  });
});

describe('timeframe menu', () => {
  it('groups every contract timeframe exactly once', () => {
    const grouped = TIMEFRAME_GROUPS.flatMap(group => group.timeframes);
    expect([...grouped].sort()).toEqual([...TIMEFRAMES].sort());
    expect(grouped).toHaveLength(TIMEFRAMES.length);
  });

  it('spells out case-sensitive units', () => {
    expect(timeframeTitle('1m')).toBe('1 minute');
    expect(timeframeTitle('1M')).toBe('1 month');
    expect(timeframeTitle('15s')).toBe('15 seconds');
    expect(timeframeTitle('4h')).toBe('4 hours');
  });

  it('reads availability from venue capabilities', () => {
    expect(timeframeAvailability('1s', BINANCE)).toMatchObject({
      offered: true,
      tradeBuilt: true,
    });
    expect(timeframeAvailability('1s', BINANCE).note).toMatch(/public trades/);
    expect(timeframeAvailability('30s', BINANCE)).toMatchObject({
      offered: false,
    });
    expect(timeframeAvailability('30s', BINANCE).note).toMatch(
      /not offered by Binance/
    );
    expect(timeframeAvailability('30s', null).offered).toBe(true);
    expect(offeredTimeframes(null)).toBeNull();
  });
});

describe('bar close countdown', () => {
  it('counts down to the end of the current bar', () => {
    const now = Date.UTC(2026, 10, 22, 14, 3, 47, 250);
    expect(msUntilBarClose('5s', now)).toBe(2_750);
    expect(msUntilBarClose('5m', now)).toBe(60_000 + 12_750);
    expect(formatCountdown(msUntilBarClose('5m', now))).toBe('1:13');
  });

  it('closes monthly bars on the 1st of the next month', () => {
    const feb = Date.UTC(2026, 1, 27, 12);
    expect(feb + msUntilBarClose('1M', feb)).toBe(Date.UTC(2026, 2, 1));
    expect(formatCountdown(msUntilBarClose('1M', feb))).toBe('1d 12:00');
    const dec = Date.UTC(2026, 11, 31, 23, 59, 30);
    expect(dec + msUntilBarClose('1M', dec)).toBe(Date.UTC(2027, 0, 1));
    expect(formatCountdown(msUntilBarClose('1M', dec))).toBe('0:30');
  });

  it('formats hours with seconds', () => {
    expect(formatCountdown(3 * 3_600_000 + 4 * 60_000 + 15_000)).toBe(
      '3:04:15'
    );
    expect(formatCountdown(-5)).toBe('0:00');
  });
});

describe('calendar-aware replay', () => {
  const months = [
    Date.UTC(2025, 11, 1),
    Date.UTC(2026, 0, 1),
    Date.UTC(2026, 1, 1),
    Date.UTC(2026, 2, 1),
  ];

  it('steps month bars and asks for indicators as of the true month end', () => {
    let state = chooseReplayStart(startReplay(), months, months[0] ?? 0);
    expect(replayAsOfMs(state, '1M')).toBe(Date.UTC(2026, 0, 1));
    state = stepReplay(state, months, 2);
    expect(state.cursorMs).toBe(Date.UTC(2026, 1, 1));
    // February 2026 has 28 days.
    expect(replayAsOfMs(state, '1M')).toBe(Date.UTC(2026, 2, 1));
    expect(replayAsOfMs(state, '5s')).toBe(Date.UTC(2026, 1, 1) + 5_000);
  });
});
