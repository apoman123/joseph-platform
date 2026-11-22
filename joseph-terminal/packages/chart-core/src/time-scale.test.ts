/**
 * @fileoverview Seconds and monthly timeframes on the time axis: session
 * separator units (minutes/hours for seconds, years for months), labels
 * and the axis label options.
 */
import {shiftTimeframe} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  sessionBoundaries,
  sessionLabel,
  sessionUnitFor,
} from './session-separators';
import {timeScaleLabelsFor} from './time-scale';

/** Tuesday 2026-06-02 13:58:00 UTC. */
const T0 = Date.UTC(2026, 5, 2, 13, 58);

describe('session separators for seconds and months', () => {
  it('uses minutes on 1s, hours on 5s-30s and years on 1M', () => {
    expect(sessionUnitFor('1s')).toBe('minute');
    expect(sessionUnitFor('5s')).toBe('hour');
    expect(sessionUnitFor('30s')).toBe('hour');
    expect(sessionUnitFor('1m')).toBe('day');
    expect(sessionUnitFor('1w')).toBe('month');
    expect(sessionUnitFor('1M')).toBe('year');
  });

  it('marks every new minute on 1s bars', () => {
    const times = Array.from({length: 150}, (_, i) => T0 + i * 1_000);
    expect(sessionBoundaries(times, '1s')).toEqual([T0 + 60_000, T0 + 120_000]);
  });

  it('marks every new hour on 30s bars', () => {
    const times = Array.from({length: 10}, (_, i) => T0 + i * 30_000);
    expect(sessionBoundaries(times, '30s')).toEqual([Date.UTC(2026, 5, 2, 14)]);
  });

  it('marks January on monthly bars', () => {
    const months = Array.from({length: 15}, (_, i) =>
      shiftTimeframe('1M', Date.UTC(2025, 10, 1), i)
    );
    expect(sessionBoundaries(months, '1M')).toEqual([
      Date.UTC(2026, 0, 1),
      Date.UTC(2027, 0, 1),
    ]);
  });

  it('labels minute, hour and year sessions', () => {
    expect(sessionLabel(Date.UTC(2026, 5, 2, 14, 5), 'minute')).toBe('14:05');
    expect(sessionLabel(Date.UTC(2026, 5, 2, 14), 'hour')).toBe('14:00');
    expect(sessionLabel(Date.UTC(2026, 5, 2), 'hour')).toBe('Tue 2');
    expect(sessionLabel(Date.UTC(2027, 0, 1), 'year')).toBe('2027');
  });
});

describe('time axis labels', () => {
  it('shows seconds only for sub-minute bars and dates from 1d up', () => {
    expect(timeScaleLabelsFor('5s')).toEqual({
      timeVisible: true,
      secondsVisible: true,
    });
    expect(timeScaleLabelsFor('1m')).toEqual({
      timeVisible: true,
      secondsVisible: false,
    });
    expect(timeScaleLabelsFor('1d').timeVisible).toBe(false);
    expect(timeScaleLabelsFor('1M')).toEqual({
      timeVisible: false,
      secondsVisible: false,
    });
  });
});
