/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Unit and property tests for exact decimal arithmetic and the
 * instrument identity helpers.
 */

import {describe, expect, it} from 'vitest';
import {Decimal, stepDecimals} from '../src/decimal';
import {
  VENUES,
  VENUE_NAMES,
  floorToTimeframe,
  formatInstrumentId,
  isInstrumentId,
  parseInstrumentId,
} from '../src/identity';

describe('Decimal', () => {
  it('adds without binary float error', () => {
    expect(Decimal.parse('0.1').add(Decimal.parse('0.2')).toString()).toBe(
      '0.3'
    );
  });

  it('floors quantities to the lot size', () => {
    expect(
      Decimal.parse('0.01999').floorToStep(Decimal.parse('0.001')).toString()
    ).toBe('0.019');
    expect(
      Decimal.parse('-1.5').floorToStep(Decimal.parse('1')).toString()
    ).toBe('-2');
  });

  it('rounds prices to the tick, ties to even', () => {
    const tick = Decimal.parse('0.5');
    expect(Decimal.parse('100.24').roundToStep(tick).toString()).toBe('100');
    expect(Decimal.parse('100.26').roundToStep(tick).toString()).toBe('100.5');
    expect(Decimal.parse('100.25').roundToStep(tick).toString()).toBe('100');
    expect(Decimal.parse('100.75').roundToStep(tick).toString()).toBe('101');
  });

  it('rejects non-decimal text', () => {
    expect(() => Decimal.parse('1e5')).toThrow(RangeError);
    expect(() => Decimal.parse('abc')).toThrow(RangeError);
    expect(Decimal.isValid('94700.5')).toBe(true);
  });

  it('divides with bounded precision', () => {
    expect(Decimal.parse('1').div(Decimal.parse('3')).toString()).toBe(
      '0.333333333333333333'
    );
    expect(() => Decimal.parse('1').div(Decimal.zero())).toThrow(RangeError);
  });

  it('formats fixed digits for display', () => {
    expect(Decimal.parse('94700.456').toFixed(2)).toBe('94700.46');
    expect(Decimal.parse('-0.004').toFixed(2)).toBe('0.00');
  });

  it('round-trips random values exactly (property)', () => {
    for (let i = 0; i < 500; i += 1) {
      const whole = Math.floor(Math.random() * 1e9);
      const frac = Math.floor(Math.random() * 1e8)
        .toString()
        .padStart(8, '0');
      const text = `${i % 2 ? '-' : ''}${whole}.${frac}`;
      const parsed = Decimal.parse(text);
      expect(Decimal.parse(parsed.toString()).eq(parsed)).toBe(true);
      expect(parsed.add(parsed).sub(parsed).eq(parsed)).toBe(true);
    }
  });

  it('counts step decimals', () => {
    expect(stepDecimals('0.010')).toBe(2);
    expect(stepDecimals('1')).toBe(0);
  });
});

describe('identity helpers', () => {
  it('parses and formats canonical ids', () => {
    const parts = parseInstrumentId('hyperliquid:perp:BTC');
    expect(parts.venue).toBe('hyperliquid');
    expect(formatInstrumentId(parts)).toBe('hyperliquid:perp:BTC');
    expect(() => parseInstrumentId('BTCUSDT')).toThrow(RangeError);
  });

  it('knows TPEx (Taipei Exchange) listings', () => {
    expect(parseInstrumentId('tpex:etf:00679B')).toEqual({
      venue: 'tpex',
      marketType: 'etf',
      venueMarketId: '00679B',
    });
    expect(isInstrumentId('tpex:stock:6488')).toBe(true);
    expect(VENUES).toContain('tpex');
    expect(VENUES.indexOf('tpex')).toBe(VENUES.indexOf('twse') + 1);
    expect(VENUE_NAMES.tpex).toBe('Taipei Exchange (TPEx)');
  });

  it('knows Cboe BZX listings', () => {
    expect(parseInstrumentId('cboe:etf:GOVT')).toEqual({
      venue: 'cboe',
      marketType: 'etf',
      venueMarketId: 'GOVT',
    });
    expect(isInstrumentId('cboe:stock:CBOE')).toBe(true);
    expect(isInstrumentId('iex:etf:GOVT')).toBe(false);
    expect(VENUES.indexOf('cboe')).toBe(VENUES.indexOf('nyse') + 1);
    expect(VENUE_NAMES.cboe).toBe('Cboe BZX');
  });

  it('aligns weekly bars to Monday UTC', () => {
    const monday = Date.UTC(2026, 10, 16);
    expect(floorToTimeframe('1w', monday + 3 * 86_400_000)).toBe(monday);
    expect(floorToTimeframe('5m', monday + 7 * 60_000)).toBe(
      monday + 5 * 60_000
    );
  });
});
