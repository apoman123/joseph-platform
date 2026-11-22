/**
 * @fileoverview Unit tests for session separators, study panes, comparison
 * series and user colors on overlays.
 */
import {describe, expect, it} from 'vitest';
import {paintOverlays} from './overlay-painter';
import {
  createSessionSeparatorLayer,
  sessionBoundaries,
  sessionLabel,
  sessionUnitFor,
} from './session-separators';
import {FakeChartPort, createRecordingContext} from './testing/fake-chart-port';
import {DEFAULT_CHART_THEME} from './theme';

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
/** Monday 2026-06-01 00:00 UTC. */
const MONDAY = Date.UTC(2026, 5, 1);

describe('session separators', () => {
  it('picks days below 1d, weeks on 1d and months on 1w', () => {
    expect(sessionUnitFor('5m')).toBe('day');
    expect(sessionUnitFor('12h')).toBe('day');
    expect(sessionUnitFor('1d')).toBe('week');
    expect(sessionUnitFor('1w')).toBe('month');
  });

  it('marks the first bar of every new UTC day', () => {
    const times = Array.from(
      {length: 30},
      (_, i) => MONDAY - 8 * HOUR + i * 4 * HOUR
    );
    const boundaries = sessionBoundaries(times, '4h');
    expect(boundaries).toEqual([
      MONDAY,
      MONDAY + DAY,
      MONDAY + 2 * DAY,
      MONDAY + 3 * DAY,
      MONDAY + 4 * DAY,
    ]);
  });

  it('starts weeks on Monday and months on the 1st', () => {
    const days = Array.from({length: 15}, (_, i) => MONDAY - 3 * DAY + i * DAY);
    expect(sessionBoundaries(days, '1d')).toEqual([MONDAY, MONDAY + 7 * DAY]);
    const weeks = [
      Date.UTC(2026, 4, 25),
      Date.UTC(2026, 5, 1),
      Date.UTC(2026, 5, 8),
    ];
    expect(sessionBoundaries(weeks, '1w')).toEqual([Date.UTC(2026, 5, 1)]);
  });

  it('labels sessions in UTC', () => {
    expect(sessionLabel(MONDAY, 'day')).toBe('Mon 1');
    expect(sessionLabel(MONDAY, 'week')).toBe('Mon 1 Jun');
    expect(sessionLabel(MONDAY, 'month')).toBe('Jun 2026');
  });

  it('paints one dotted line per visible boundary and skips crowded labels', () => {
    const {ctx, calls} = createRecordingContext();
    const times = Array.from(
      {length: 48},
      (_, i) => MONDAY - 12 * HOUR + i * HOUR
    );
    const layer = createSessionSeparatorLayer({
      times: () => times,
      timeframe: () => '1h',
      color: () => '#8fa5b3',
      fontFamily: () => 'Archivo',
    });
    layer.draw(ctx, {
      width: 1000,
      height: 400,
      timeToX: t => ((t - (times[0] ?? 0)) / HOUR) * 20,
      priceToY: () => 0,
      xToTime: () => null,
      yToPrice: () => null,
    });
    const lines = calls.filter(c => c.op === 'lineTo');
    expect(lines).toHaveLength(2);
    expect(lines[0]?.args).toEqual([240.5, 400]);
    expect(calls.filter(c => c.op === 'fillText').map(c => c.args[0])).toEqual([
      'Mon 1',
      'Tue 2',
    ]);
    expect(layer.zIndex).toBeLessThan(100);
  });
});

describe('study panes and comparisons on the fake port', () => {
  it('stores, replaces and removes study panes', () => {
    const port = new FakeChartPort();
    port.setStudyPane({
      id: 'delta',
      title: 'Delta %',
      style: 'histogram',
      points: [],
    });
    port.setStudyPane({
      id: 'delta',
      title: 'Delta %',
      style: 'histogram',
      points: [{timeMs: 0, value: 12, tone: 'bid'}],
    });
    expect(port.studyPanes.get('delta')?.points).toHaveLength(1);
    port.removeStudyPane('delta');
    expect(port.studyPanes.size).toBe(0);
  });

  it('clears studies and comparisons when the instrument or timeframe changes', () => {
    const port = new FakeChartPort();
    const config = {
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m' as const,
      chartType: 'candles' as const,
      priceScale: 'linear' as const,
      tickSize: '0.1',
      showVolume: true,
    };
    port.setSeries(config);
    port.setStudyPane({
      id: 'delta',
      title: 'Delta %',
      style: 'histogram',
      points: [],
    });
    port.setCompareSeries([
      {id: 'eth', label: 'ETHUSD', color: '#ffffff', points: []},
    ]);
    port.setSeries({...config, timeframe: '1h'});
    expect(port.studyPanes.size).toBe(0);
    expect(port.compare).toEqual([]);
  });
});

describe('user colors on overlays', () => {
  it('uses the model color instead of the tone when present', () => {
    const {ctx, calls} = createRecordingContext();
    paintOverlays(
      ctx,
      [
        {
          layerId: 'zones',
          zIndex: 1,
          visible: true,
          models: [
            {
              kind: 'price-line',
              id: 'ray',
              price: '150',
              tone: 'bid',
              color: '#123456',
              lineStyle: 'dashed',
              label: 'Swing high',
              fromMs: 0,
            },
          ],
        },
      ],
      {
        width: 500,
        height: 300,
        timeToX: () => 10,
        priceToY: () => 50,
        xToTime: () => null,
        yToPrice: () => null,
      },
      DEFAULT_CHART_THEME
    );
    const strokes = calls
      .filter(c => c.op === 'set:strokeStyle')
      .map(c => c.args[0]);
    expect(strokes).toContain('#123456');
    expect(strokes).not.toContain(DEFAULT_CHART_THEME.bid);
  });
});
