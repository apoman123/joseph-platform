/**
 * @fileoverview Unit tests for chart-core transforms, geometry and fakes.
 */
import {describe, expect, it} from 'vitest';
import {toLwcMarker} from './adapters/lwc/lwc-chart-port';
import type {ChartBar} from './chart-port';
import {groupCaptions, layoutBox, paintOverlays} from './overlay-painter';
import {FakeChartPort, createRecordingContext} from './testing/fake-chart-port';
import {DEFAULT_CHART_THEME, themeFromCss, toneColor, withAlpha} from './theme';
import {TimeIndex} from './time-index';
import {heikinAshiBar, toHeikinAshi} from './transforms/heikin-ashi';

/**
 * Builds a bar.
 * @param i Bar index (minute).
 * @param o Open.
 * @param h High.
 * @param l Low.
 * @param c Close.
 * @return Bar.
 */
function bar(i: number, o: number, h: number, l: number, c: number): ChartBar {
  return {
    timeMs: i * 60_000,
    open: o,
    high: h,
    low: l,
    close: c,
    volume: 1,
    final: true,
  };
}

describe('Heikin Ashi transform', () => {
  it('computes HA close/open from raw prices and the previous HA bar', () => {
    const first = heikinAshiBar(bar(0, 10, 14, 8, 12), null);
    expect(first.close).toBe(11);
    expect(first.open).toBe(11);
    const second = heikinAshiBar(bar(1, 12, 16, 11, 15), first);
    expect(second.open).toBe(11);
    expect(second.close).toBe(13.5);
    expect(second.high).toBe(16);
    expect(second.low).toBe(11);
  });

  it('keeps length, time and volume and never mutates raw bars', () => {
    const raw = [bar(0, 1, 2, 0.5, 1.5), bar(1, 1.5, 3, 1, 2.5)];
    const copy = JSON.parse(JSON.stringify(raw));
    const ha = toHeikinAshi(raw);
    expect(ha.map(b => b.timeMs)).toEqual([0, 60_000]);
    expect(raw).toEqual(copy);
  });
});

describe('TimeIndex', () => {
  const index = new TimeIndex(
    [{timeMs: 0}, {timeMs: 60_000}, {timeMs: 180_000}],
    60_000
  );

  it('maps bar times to integer logical indices', () => {
    expect(index.toLogical(0)).toBe(0);
    expect(index.toLogical(60_000)).toBe(1);
    expect(index.toLogical(180_000)).toBe(2);
  });

  it('interpolates inside gaps and extrapolates beyond the data', () => {
    expect(index.toLogical(30_000)).toBe(0.5);
    expect(index.toLogical(300_000)).toBe(4);
    expect(index.toLogical(-60_000)).toBe(-1);
    expect(index.toTime(4)).toBe(300_000);
    expect(index.toTime(0.5)).toBe(30_000);
  });

  it('snaps to bar starts', () => {
    expect(index.snap(75_000)).toBe(60_000);
    expect(new TimeIndex([], 60_000).snap(1)).toBeNull();
  });
});

describe('theme', () => {
  it('reads CSS tokens with fallbacks', () => {
    const theme = themeFromCss(name => (name === '--bid' ? ' #00ff00 ' : ''));
    expect(theme.bid).toBe('#00ff00');
    expect(theme.ask).toBe(DEFAULT_CHART_THEME.ask);
  });

  it('maps tones and alpha', () => {
    expect(toneColor(DEFAULT_CHART_THEME, 'user')).toBe('#e66fd2');
    expect(toneColor(DEFAULT_CHART_THEME, 'danger')).toBe(
      DEFAULT_CHART_THEME.ask
    );
    expect(withAlpha('#ff0000', 0.5)).toBe('rgba(255, 0, 0, 0.5)');
    expect(withAlpha('red', 0.5)).toBe('red');
  });
});

describe('overlay painter', () => {
  const port = new FakeChartPort();
  const transform = port.transform();

  it('lays out boxes from time/price and extends open boxes to the edge', () => {
    const rect = layoutBox(
      {
        kind: 'box',
        id: 'b',
        fromMs: 60_000,
        toMs: null,
        lower: '150',
        upper: '160',
        tone: 'bid',
        fillOpacity: 0.2,
        lineStyle: 'solid',
      },
      transform
    );
    expect(rect).toEqual({x: 10, y: 200, width: 990, height: 50});
  });

  it('paints boxes, rays and captions in z-order', () => {
    const {ctx, calls} = createRecordingContext();
    paintOverlays(
      ctx,
      [
        {
          layerId: 'top',
          zIndex: 2,
          visible: true,
          caption: 'as of 12:00 UTC',
          models: [
            {
              kind: 'price-line',
              id: 'r',
              price: '150',
              tone: 'user',
              lineStyle: 'solid',
              label: 'Ray',
              fromMs: 0,
            },
          ],
        },
        {
          layerId: 'hidden',
          zIndex: 1,
          visible: false,
          models: [
            {
              kind: 'box',
              id: 'h',
              fromMs: 0,
              toMs: 60_000,
              lower: '1',
              upper: '2',
              tone: 'ask',
              fillOpacity: 1,
              lineStyle: 'solid',
            },
          ],
        },
      ],
      transform,
      DEFAULT_CHART_THEME
    );
    const texts = calls.filter(c => c.op === 'fillText').map(c => c.args[0]);
    expect(texts).toEqual(['Ray', 'as of 12:00 UTC']);
    // Only the caption plate is filled; the hidden layer's box is not drawn.
    expect(calls.filter(c => c.op === 'fillRect')).toHaveLength(1);
  });
});

describe('caption grouping', () => {
  it('merges layers that share provenance into one line', () => {
    expect(
      groupCaptions([
        'FVG and IFVG, as of 12:05 UTC (live)',
        'Liquidity, as of 12:05 UTC (live)',
        'Order flow, as of 12:00 UTC (live)',
        'custom caption',
      ])
    ).toEqual([
      'custom caption',
      '2 layers as of 12:05 UTC (live)',
      'Order flow as of 12:00 UTC (live)',
    ]);
  });
});

describe('FakeChartPort', () => {
  it('ignores out-of-order bars and clears overlays on instrument switch', () => {
    const port = new FakeChartPort();
    port.setSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '1m',
      chartType: 'candles',
      priceScale: 'linear',
      tickSize: '0.1',
      showVolume: true,
    });
    port.setBars([bar(1, 1, 1, 1, 1), bar(0, 1, 1, 1, 1), bar(1, 2, 2, 2, 2)]);
    expect(port.bars.map(b => b.timeMs)).toEqual([0, 60_000]);
    port.updateBar(bar(0, 9, 9, 9, 9));
    expect(port.bars[0]?.open).toBe(1);
    port.setOverlayLayer({layerId: 'l', zIndex: 0, visible: true, models: []});
    port.setSeries({
      instrumentId: 'mock:perp:ETHUSD',
      timeframe: '1m',
      chartType: 'candles',
      priceScale: 'linear',
      tickSize: '0.01',
      showVolume: true,
    });
    expect(port.layers.size).toBe(0);
    expect(port.bars).toEqual([]);
  });

  it('tracks listeners for leak checks', () => {
    const port = new FakeChartPort();
    const off = port.on('click', () => undefined);
    expect(port.listenerCount()).toBe(1);
    off();
    expect(port.listenerCount()).toBe(0);
  });
});

describe('LWC marker mapping', () => {
  it('maps marker shapes, positions and snaps time to bars', () => {
    const marker = toLwcMarker(
      {
        kind: 'marker',
        id: 'm',
        timeMs: 90_000,
        position: 'below',
        shape: 'arrow-up',
        tone: 'bid',
        text: 'SSL sweep',
      },
      DEFAULT_CHART_THEME,
      t => Math.floor(t / 60_000) * 60_000
    );
    expect(marker).toMatchObject({
      time: 60,
      position: 'belowBar',
      shape: 'arrowUp',
      text: 'SSL sweep',
    });
  });
});
