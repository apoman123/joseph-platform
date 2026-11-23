/**
 * @fileoverview Tests for the engine-backed regression channel tool:
 * request building, point selection at the t2 bar, engine refusals,
 * rendering, hit-testing and the data-window rows.
 */
import {
  HttpMarketDataClient,
  type IndicatorSeriesPoint,
} from '@joseph/contracts';
import {type DrawingSeriesState} from '@joseph/drawing-core';
import {describe, expect, it} from 'vitest';
import {
  STATE,
  TRANSFORM,
  makeDoc,
  manifestProblems,
  type Op,
} from '../testing/harness';
import {
  SAME_BAR_MESSAGE,
  channelView,
  regressionChannelTool as tool,
  regressionRequest,
} from '.';

/** t2 placed first (minute 10), t1 second (minute 2) to test ordering. */
const DOC = makeDoc(tool, [
  {time_ms: 600_000, price: '160'},
  {time_ms: 120_000, price: '140'},
]);

/** Channel point published at the t2 bar. */
const CHANNEL: IndicatorSeriesPoint = {
  bar_start_ms: 600_000,
  values: {
    start_ms: 120_000,
    start: '140',
    end: '160',
    upper_start: '150',
    upper_end: '170',
    lower_start: '130',
    lower_end: '150',
    slope: '2.5',
    stdev: '5',
    pearson_r: '0.8734',
    max_up: '7',
    max_down: '6',
    bars: 9,
  },
};

/**
 * Builds a ready series state.
 * @param points Points.
 * @param asOfMs As-of time.
 * @return State.
 */
function ready(
  points: IndicatorSeriesPoint[],
  asOfMs = 900_000
): DrawingSeriesState {
  const request = regressionRequest(DOC, '1m');
  if (!request) throw new Error('no request');
  return {
    status: 'ready',
    request,
    timeframe: '1m',
    points,
    asOfMs,
    error: null,
  };
}

/**
 * Collects drawn texts with a series.
 * @param series Series state.
 * @return Texts.
 */
function drawnTexts(series: DrawingSeriesState | null): string[] {
  const ops: Op[] = [];
  const ctx = new Proxy({} as Record<string, unknown>, {
    get(_target, prop: string) {
      if (prop === 'measureText')
        return (text: string) => ({width: text.length * 6});
      return (...args: unknown[]) => ops.push({op: prop, args});
    },
    set: () => true,
  }) as unknown as CanvasRenderingContext2D;
  tool.render(ctx, DOC, TRANSFORM, {...STATE, series});
  return ops.filter(o => o.op === 'fillText').map(o => String(o.args[0]));
}

describe('regression-channel', () => {
  it('has a valid manifest with two anchors', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest).toMatchObject({group: 'studies', anchorCount: 2});
  });

  it('requests from the t2 bar with t1 as the anchor, in time order', () => {
    expect(regressionRequest(DOC, '1m')).toEqual({
      pluginId: 'regression_channel',
      fromMs: 600_000,
      settings: {anchor_ms: 120_000, source: 'close', deviations: '2'},
    });
    const wide = {...DOC, props: {deviations: '3', show_pearson: false}};
    expect(regressionRequest(wide, '1m')?.settings.deviations).toBe('3');
  });

  it('sends the documented GET through the market client', async () => {
    const urls: string[] = [];
    const client = new HttpMarketDataClient({
      baseUrl: 'http://engine.test',
      fetchImpl: async url => {
        urls.push(String(url));
        return new Response('{"points":[]}', {status: 200});
      },
    });
    const request = regressionRequest(DOC, '1m');
    if (!request) throw new Error('no request');
    await client.getIndicatorSeries({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '1m',
      pluginId: request.pluginId,
      fromMs: request.fromMs,
      settings: request.settings,
    });
    const params = Object.fromEntries(new URL(urls[0] ?? '').searchParams);
    expect(params).toMatchObject({
      plugin: 'regression_channel',
      from_ms: '600000',
      settings:
        '{"regression_channel":{"anchor_ms":120000,"source":"close","deviations":"2"}}',
    });
  });

  it('takes the point of the t2 bar and draws the channel with R', () => {
    const series = ready([{...CHANNEL, bar_start_ms: 540_000}, CHANNEL]);
    const view = channelView(DOC, series);
    expect(view).toMatchObject({
      kind: 'ready',
      values: {startMs: 120_000, endMs: 600_000, end: '160', bars: 9},
    });
    expect(drawnTexts(series)).toEqual(["Pearson's R 0.873"]);
    const hidden = {...DOC, props: {show_pearson: false}};
    const ops: string[] = [];
    tool.render(
      new Proxy({} as Record<string, unknown>, {
        get: (_t, prop: string) =>
          prop === 'measureText'
            ? () => ({width: 10})
            : (...args: unknown[]) =>
                prop === 'fillText' && ops.push(String(args[0])),
        set: () => true,
      }) as unknown as CanvasRenderingContext2D,
      hidden,
      TRANSFORM,
      {...STATE, series}
    );
    expect(ops).toEqual([]);
  });

  it('shows a notice for null values or a missing t2 point, not a bar cap', () => {
    const text = 'No engine values for these anchors yet';
    const nulls = ready([
      {
        bar_start_ms: 600_000,
        values: {...CHANNEL.values, start_ms: null, start: null, end: null},
      },
    ]);
    expect(channelView(DOC, nulls)).toEqual({kind: 'notice', text});
    const clipped = ready([{...CHANNEL, bar_start_ms: 660_000}]);
    expect(channelView(DOC, clipped)).toEqual({kind: 'notice', text});
    expect(drawnTexts(clipped)).toEqual([text]);
    expect(tool.describe?.(DOC, {series: clipped, tickSize: null})).toBe(text);
  });

  it('shows the engine message when the engine refuses the anchors', () => {
    const message = 'Anchor is more than 50,000 bars back.';
    const refused = {
      ...ready([]),
      status: 'error' as const,
      error: `VALIDATION_FAILED: ${message}`,
      refusal: message,
    };
    expect(channelView(DOC, refused)).toEqual({kind: 'notice', text: message});
    expect(
      tool.dataRows?.(DOC, 600_000, {series: refused, tickSize: null})
    ).toEqual([{label: 'Regression', value: 'refused by the engine'}]);
  });

  it('asks for anchors on different bars and waits for t2 to close', () => {
    const same = makeDoc(tool, [
      {time_ms: 600_000, price: '160'},
      {time_ms: 610_000, price: '150'},
    ]);
    expect(channelView(same, ready([CHANNEL]))).toEqual({
      kind: 'notice',
      text: SAME_BAR_MESSAGE,
    });
    expect(channelView(DOC, ready([], 640_000))).toMatchObject({
      text: 'Waiting for the end bar to close',
    });
  });

  it('hits handles on the centre line ends, the lines and the filled inside', () => {
    const series = ready([CHANNEL]);
    // Centre from (20, 300) [t1 = anchor 1] to (100, 200) [t2 = anchor 0].
    expect(tool.hitTest(DOC, TRANSFORM, {x: 100, y: 200}, 6, series)).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
    expect(tool.hitTest(DOC, TRANSFORM, {x: 20, y: 300}, 6, series)).toEqual({
      part: 'handle',
      handleIndex: 1,
    });
    // Upper line runs (20, 250) → (100, 150), so it is at y = 200 at x = 60.
    expect(tool.hitTest(DOC, TRANSFORM, {x: 60, y: 202}, 6, series)).toEqual({
      part: 'body',
    });
    // Between the rails (centre 250, lower 300 at x = 60) the fill is hit.
    expect(tool.hitTest(DOC, TRANSFORM, {x: 60, y: 275}, 6, series)).toEqual({
      part: 'body',
    });
    const unfilled = {...DOC, style: {...DOC.style, fill_color: null}};
    expect(
      tool.hitTest(unfilled, TRANSFORM, {x: 60, y: 275}, 6, series)
    ).toBeNull();
    expect(
      tool.hitTest(DOC, TRANSFORM, {x: 200, y: 280}, 6, series)
    ).toBeNull();
  });

  it('lists the channel values for the data window and object list', () => {
    const series = ready([CHANNEL]);
    expect(tool.dataRows?.(DOC, 0, {series, tickSize: '0.1'})).toEqual([
      {label: 'Regression end', value: '160.0'},
      {label: 'Channel upper', value: '170.0'},
      {label: 'Channel lower', value: '150.0'},
      {label: "Pearson's R", value: '0.873'},
    ]);
    expect(tool.describe?.(DOC, {series, tickSize: '0.1'})).toBe(
      'R 0.873 · end 160.0 · 9 bars'
    );
  });
});
