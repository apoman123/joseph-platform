/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the engine-backed anchored VWAP tool: request
 * building, series interpretation (incl. engine refusals), rendering,
 * hit-testing and the data-window rows.
 */

import {
  SERIES_LOADING_MESSAGE,
  type DrawingSeriesPoint,
  type DrawingSeriesState,
} from '@joseph/drawing-core';
import {describe, expect, it} from 'vitest';
import {
  STATE,
  TRANSFORM,
  makeDoc,
  manifestProblems,
  texts,
  type Op,
} from '../testing/harness';
import {anchoredVwapRequest, anchoredVwapTool as tool, vwapView} from '.';

/** Anchor in the middle of the 1m bar starting at 2 minutes. */
const DOC = makeDoc(tool, [{time_ms: 140_000, price: '120'}]);

/**
 * Builds a ready 1m series state for the fixture drawing.
 * @param points Series points.
 * @param asOfMs Response as-of time, epoch milliseconds.
 * @return Ready series state.
 * @throws Error when the fixture drawing yields no series request.
 */
function ready(
  points: DrawingSeriesPoint[],
  asOfMs = 600_000
): DrawingSeriesState {
  const request = anchoredVwapRequest(DOC, '1m');
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
 * Builds a point with VWAP and band 1 values (band 1 at VWAP +/- 2).
 * @param minute Bar start, in minutes since the epoch.
 * @param vwap VWAP, or null for a bar without values.
 * @return Series point.
 */
function point(minute: number, vwap: number | null): DrawingSeriesPoint {
  return {
    bar_start_ms: minute * 60_000,
    values: {
      'VWAP': vwap === null ? null : String(vwap),
      'Stdev': vwap === null ? null : '2',
      'Upper 1': vwap === null ? null : String(vwap + 2),
      'Lower 1': vwap === null ? null : String(vwap - 2),
      'Bars': vwap === null ? null : String(minute - 1),
    },
  };
}

const READY = ready([point(2, 150), point(3, 152), point(4, 154)]);

/**
 * Renders the fixture drawing with a series into a recording context.
 * @param series Series state, or null before any request.
 * @return Recorded operations in call order.
 */
function render(series: DrawingSeriesState | null): Op[] {
  const ops: Op[] = [];
  // Deliberate partial mock: the proxy stands in for every context member.
  const ctx = new Proxy(
    {},
    {
      get(_target, prop: string) {
        if (prop === 'measureText') {
          return (text: string) => ({width: text.length * 6});
        }
        return (...args: unknown[]) => ops.push({op: prop, args});
      },
      set(_target, prop: string, value: unknown) {
        ops.push({op: `set:${prop}`, args: [value]});
        return true;
      },
    }
  ) as unknown as CanvasRenderingContext2D;
  tool.render(ctx, DOC, TRANSFORM, {...STATE, series});
  return ops;
}

describe('anchored-vwap', () => {
  it('has a valid manifest in the engine studies group', () => {
    expect(manifestProblems(tool)).toEqual([]);
    expect(tool.manifest).toMatchObject({group: 'studies', anchorCount: 1});
  });

  it('runs the built-in script from the anchor bar', () => {
    expect(anchoredVwapRequest(DOC, '1m')).toEqual({
      scriptId: 'builtin:anchored_vwap',
      fromMs: 120_000,
      inputs: {
        anchor: 'time',
        anchor_ms: 120_000,
        source: 'hlc3',
        band_1: 1,
        band_2: 0,
        band_3: 0,
      },
    });
    const banded = {...DOC, props: {band_1: '0', band_2: '2', band_3: '9'}};
    expect(anchoredVwapRequest(banded, '5m')?.inputs).toMatchObject({
      anchor_ms: 0,
      band_1: 0,
      band_2: 2,
      band_3: 0,
    });
    expect(tool.seriesRequest?.(DOC, {timeframe: '1m'})?.fromMs).toBe(120_000);
  });

  it('draws the VWAP line and enabled bands from the anchor forward', () => {
    const view = vwapView(READY);
    expect(view.kind).toBe('ready');
    if (view.kind !== 'ready') return;
    expect(view.line.map(p => p.value)).toEqual([150, 152, 154]);
    expect(view.bands.map(b => b.k)).toEqual([1]);
    expect(view.latest).toBe('154');
    const ops = render(READY);
    expect(texts(ops)).toContain('VWAP 154.0');
    expect(ops.filter(o => o.op === 'lineTo').length).toBeGreaterThan(5);
  });

  it('replaces the drawing with a notice when values come back null', () => {
    const far = ready([point(2, null), point(3, null)]);
    const text = 'No engine values for this anchor yet';
    expect(vwapView(far)).toEqual({kind: 'notice', anchorBarMs: 120_000, text});
    expect(texts(render(far))).toEqual([text]);
    expect(
      tool.dataRows?.(DOC, 120_000, {series: far, tickSize: '0.1'})
    ).toEqual([]);
    expect(tool.describe?.(DOC, {series: far, tickSize: '0.1'})).toBe(text);
  });

  it('shows the engine message only when the engine refuses the anchor', () => {
    const message =
      'Anchor is more than 50,000 bars back; the engine computes anchored values from at most 50,000 bars.';
    const refused: DrawingSeriesState = {
      ...ready([]),
      status: 'error',
      error: `VALIDATION_FAILED: ${message}`,
      refusal: message,
    };
    expect(vwapView(refused)).toMatchObject({kind: 'notice', text: message});
    expect(texts(render(refused))).toEqual([message]);
    expect(
      tool.dataRows?.(DOC, 120_000, {series: refused, tickSize: '0.1'})
    ).toEqual([{label: 'Anchored VWAP', value: 'refused by the engine'}]);
    // A network failure is not a refusal.
    const failed = {...refused, refusal: null, error: 'offline'};
    expect(vwapView(failed)).toMatchObject({
      text: 'Engine values unavailable: offline',
    });
  });

  it('waits for the anchor bar to close and reports loading', () => {
    expect(vwapView(ready([], 150_000))).toMatchObject({
      text: 'Waiting for the anchor bar to close',
    });
    expect(vwapView(null)).toMatchObject({text: SERIES_LOADING_MESSAGE});
    expect(texts(render({...READY, status: 'loading', points: []}))).toEqual([
      SERIES_LOADING_MESSAGE,
    ]);
  });

  it('hits the handle on the anchor bar VWAP, the line, and misses elsewhere', () => {
    // Handle at (2 min, 150) = (20, 250); line point at (3 min, 152).
    expect(tool.hitTest(DOC, TRANSFORM, {x: 20, y: 250}, 6, READY)).toEqual({
      part: 'handle',
      handleIndex: 0,
    });
    expect(tool.hitTest(DOC, TRANSFORM, {x: 30, y: 240}, 6, READY)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(DOC, TRANSFORM, {x: 30, y: 100}, 6, READY)).toBeNull();
    // Without values the anchor marker (a vertical line) is the body.
    const far = ready([point(2, null)]);
    expect(tool.hitTest(DOC, TRANSFORM, {x: 21, y: 40}, 6, far)).toEqual({
      part: 'body',
    });
    // So is the notice plate (beside the anchor at y = 400), which keeps a
    // drawing selectable when its anchor is scrolled off-screen.
    expect(tool.hitTest(DOC, TRANSFORM, {x: 200, y: 400}, 6, far)).toEqual({
      part: 'body',
    });
    expect(tool.hitTest(DOC, TRANSFORM, {x: 200, y: 300}, 6, far)).toBeNull();
  });

  it('lists data-window rows and an object-list summary', () => {
    expect(
      tool.dataRows?.(DOC, 180_000, {series: READY, tickSize: '0.1'})
    ).toEqual([
      {label: 'Anchored VWAP', value: '152.0'},
      {label: 'VWAP +1σ', value: '154.0'},
      {label: 'VWAP −1σ', value: '150.0'},
    ]);
    expect(tool.dataRows?.(DOC, 0, {series: READY, tickSize: '0.1'})).toEqual(
      []
    );
    expect(tool.describe?.(DOC, {series: READY, tickSize: '0.1'})).toBe(
      'VWAP 154.0 from 1970-01-01 00:02 UTC · bands 1σ'
    );
  });
});
