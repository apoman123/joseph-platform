/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests of the chart pane's drawing controller,
 * with the chart library replaced by `FakeChartPort`.
 */

import {FakeChartPort, type ChartPointerEvent} from '@joseph/chart-core';
import {
  parseInstrumentId,
  type InstrumentList,
  type StreamHandler,
} from '@joseph/contracts';
import {activeChart} from '@joseph/terminal-shell';
import {act, fireEvent, render, waitFor} from '@testing-library/react';
import {describe, expect, it, vi} from 'vitest';
import {composeFeatures} from '../composition/features';
import {createRuntime} from '../composition/runtime';
import {ChartPane} from './chart-pane';
import {RuntimeProvider} from './runtime-context';

vi.mock('@joseph/chart-core/lwc', async () => {
  const core = await import('@joseph/chart-core');
  return {createLwcChartPort: () => new core.FakeChartPort()};
});

/** DEMO configuration. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

/**
 * Builds a primary-button pointer event at a point of the fake chart.
 * @param port Fake chart that maps the point to a time and price.
 * @param x X in CSS pixels.
 * @param y Y in CSS pixels.
 * @return Pointer event.
 */
function press(port: FakeChartPort, x: number, y: number): ChartPointerEvent {
  const transform = port.transform();
  return {
    x,
    y,
    timeMs: transform.xToTime(x),
    price: transform.yToPrice(y),
    button: 0,
    shiftKey: false,
    modKey: false,
    preventDefault: () => undefined,
  };
}

describe('ChartPane drawings', () => {
  it('retries failed history without reloading the terminal', async () => {
    const runtime = createRuntime(composeFeatures, DEMO);
    await runtime.registry.activateAll();
    const chart = activeChart(runtime.workspace.get());
    const batch = await runtime.clients.market.getCandles({
      instrumentId: chart.instrument_id,
      timeframe: chart.timeframe,
    });
    const getCandles = vi
      .spyOn(runtime.clients.market, 'getCandles')
      .mockRejectedValueOnce(new Error('History timed out'))
      .mockResolvedValue(batch);
    vi.spyOn(runtime.clients.marketStream, 'subscribe').mockReturnValue(
      () => undefined
    );
    const view = render(
      <RuntimeProvider runtime={runtime}>
        <ChartPane
          chart={chart}
          active
          runtime={runtime}
          onMenu={() => undefined}
        />
      </RuntimeProvider>
    );
    await waitFor(
      () => void expect(view.getByText('History timed out')).toBeTruthy()
    );
    fireEvent.click(view.getByRole('button', {name: 'Retry candles'}));
    await waitFor(
      () => void expect(view.queryByText('Candles didn’t load')).toBeNull()
    );
    const port = runtime.charts.get(chart.chart_id);
    if (!(port instanceof FakeChartPort)) throw new Error('no fake chart');
    expect(port.bars.length).toBe(batch.candles.length);
    expect(getCandles).toHaveBeenCalledTimes(2);
  });

  it('cancels old history and resubscribes when the feed mode changes', async () => {
    const runtime = createRuntime(composeFeatures, DEMO);
    await runtime.registry.activateAll();
    const chart = activeChart(runtime.workspace.get());
    const batch = await runtime.clients.market.getCandles({
      instrumentId: chart.instrument_id,
      timeframe: chart.timeframe,
    });
    let finishOld: (value: typeof batch) => void = () => undefined;
    const getCandles = vi
      .spyOn(runtime.clients.market, 'getCandles')
      .mockReturnValueOnce(
        new Promise(resolve => {
          finishOld = resolve;
        })
      )
      .mockResolvedValue({...batch, candles: []});
    const unsubscribe = vi.fn();
    const subscribe = vi
      .spyOn(runtime.clients.marketStream, 'subscribe')
      .mockReturnValue(unsubscribe);
    const view = render(
      <RuntimeProvider runtime={runtime}>
        <ChartPane
          chart={chart}
          active
          runtime={runtime}
          onMenu={() => undefined}
        />
      </RuntimeProvider>
    );
    await waitFor(() => void expect(getCandles).toHaveBeenCalledTimes(1));
    const oldSignal = getCandles.mock.calls[0]?.[0].signal;
    view.rerender(
      <RuntimeProvider runtime={runtime}>
        <ChartPane
          chart={{...chart, feed_mode: 'REST_POLL'}}
          active
          runtime={runtime}
          onMenu={() => undefined}
        />
      </RuntimeProvider>
    );
    await waitFor(() => void expect(getCandles).toHaveBeenCalledTimes(2));
    expect(oldSignal?.aborted).toBe(true);
    expect(unsubscribe).toHaveBeenCalled();
    expect(subscribe).toHaveBeenLastCalledWith(
      [expect.objectContaining({feed_mode: 'REST_POLL'})],
      expect.any(Function)
    );
    await act(async () => void finishOld(batch));
    const port = runtime.charts.get(chart.chart_id);
    if (!(port instanceof FakeChartPort)) throw new Error('no fake chart');
    expect(port.bars).toEqual([]);
    expect(view.getByText('No candles for this range')).toBeTruthy();
    view.unmount();
    expect(getCandles.mock.calls[1]?.[0].signal?.aborted).toBe(true);
  });

  it('keeps streamed prices visible when a slow history request fails', async () => {
    const runtime = createRuntime(composeFeatures, DEMO);
    await runtime.registry.activateAll();
    const chart = activeChart(runtime.workspace.get());
    const batch = await runtime.clients.market.getCandles({
      instrumentId: chart.instrument_id,
      timeframe: chart.timeframe,
    });
    let failHistory: (reason: Error) => void = () => undefined;
    vi.spyOn(runtime.clients.market, 'getCandles').mockReturnValue(
      new Promise((_resolve, reject) => {
        failHistory = reject;
      })
    );
    let receive: StreamHandler = () => undefined;
    vi.spyOn(runtime.clients.marketStream, 'subscribe').mockImplementation(
      (channels, handler) => {
        if (channels.some(channel => channel.channel === 'candles')) {
          receive = handler;
        }
        return () => undefined;
      }
    );
    const view = render(
      <RuntimeProvider runtime={runtime}>
        <ChartPane
          chart={chart}
          active
          runtime={runtime}
          onMenu={() => undefined}
        />
      </RuntimeProvider>
    );
    await waitFor(
      () =>
        void expect(runtime.charts.get(chart.chart_id)).toBeInstanceOf(
          FakeChartPort
        )
    );
    const candle = batch.candles.at(-1);
    if (!candle) throw new Error('no candle fixture');
    act(
      () =>
        void receive({
          type: 'candle.updated',
          event_id: 'live',
          schema_version: 1,
          sequence: 1,
          event_ts_ms: Date.now(),
          payload: {...candle, close: '12345', feed_state: 'LIVE'},
        })
    );
    await act(async () => void failHistory(new Error('History timed out')));
    const port = runtime.charts.get(chart.chart_id);
    if (!(port instanceof FakeChartPort)) throw new Error('no fake chart');
    expect(port.bars.at(-1)?.close).toBe(12345);
    expect(view.queryByText('Candles didn’t load')).toBeNull();
    expect(view.getByRole('button', {name: 'Retry candles'})).toBeTruthy();
    expect(
      view.container.querySelector('[data-feed-state="LIVE"]')
    ).not.toBeNull();
  });

  it('updates the visible close and price scale on each live candle event', async () => {
    const runtime = createRuntime(composeFeatures, DEMO);
    await runtime.registry.activateAll();
    const chart = activeChart(runtime.workspace.get());
    const batch = await runtime.clients.market.getCandles({
      instrumentId: chart.instrument_id,
      timeframe: chart.timeframe,
    });
    let releaseHistory: (value: typeof batch) => void = () => undefined;
    const getCandles = vi
      .spyOn(runtime.clients.market, 'getCandles')
      .mockReturnValue(
        new Promise(resolve => {
          releaseHistory = resolve;
        })
      );
    let receive: StreamHandler = () => undefined;
    const unsubscribe = vi.fn();
    vi.spyOn(runtime.clients.marketStream, 'subscribe').mockImplementation(
      (channels, handler) => {
        if (
          channels.some(
            channel =>
              channel.channel === 'candles' &&
              channel.instrument_id === chart.instrument_id &&
              channel.timeframe === chart.timeframe
          )
        ) {
          receive = handler;
        }
        return unsubscribe;
      }
    );
    const view = render(
      <RuntimeProvider runtime={runtime}>
        <ChartPane
          chart={chart}
          active
          runtime={runtime}
          onMenu={() => undefined}
        />
      </RuntimeProvider>
    );
    await waitFor(
      () =>
        void expect(runtime.charts.get(chart.chart_id)).toBeInstanceOf(
          FakeChartPort
        )
    );
    const port = runtime.charts.get(chart.chart_id);
    if (!(port instanceof FakeChartPort)) throw new Error('no fake chart');
    const candle = batch.candles.at(-1);
    if (!candle) throw new Error('no candle fixture');
    for (const close of ['12345.00', '12346.00']) {
      act(() => {
        receive({
          type: 'candle.updated',
          event_id: close,
          schema_version: 1,
          sequence: 1,
          event_ts_ms: Date.now(),
          payload: {
            ...candle,
            start_ms: port.bars.at(-1)?.timeMs ?? candle.start_ms,
            close,
            high: close,
            low: '1',
            open: '100',
            is_final: false,
            feed_state: 'LIVE',
          },
        });
      });
      expect(port.bars.at(-1)?.close).toBe(Number(close));
      expect(
        view.container.querySelector('.pane__ohlc')?.textContent
      ).toContain(Number(close).toLocaleString('en-US'));
    }
    act(() => {
      port.emit('visibleRangeChange', {
        fromMs: candle.start_ms,
        toMs: candle.end_ms,
      });
    });
    expect(getCandles).toHaveBeenCalledTimes(1);
    await act(async () => {
      releaseHistory(batch);
    });
    expect(port.bars.at(-1)?.close).toBe(12346);
    expect(
      view.container.querySelector('[data-feed-state="LIVE"]')
    ).not.toBeNull();
    view.unmount();
    expect(unsubscribe).toHaveBeenCalled();
  });

  it('requests older candles when the viewport reaches the first loaded bars', async () => {
    const runtime = createRuntime(composeFeatures, DEMO);
    await runtime.registry.activateAll();
    const chart = activeChart(runtime.workspace.get());
    const market = runtime.clients.market;
    const getCandles = vi.spyOn(market, 'getCandles');
    render(
      <RuntimeProvider runtime={runtime}>
        <ChartPane
          chart={chart}
          active
          runtime={runtime}
          onMenu={() => undefined}
        />
      </RuntimeProvider>
    );
    await waitFor(() => void expect(getCandles).toHaveBeenCalled());
    const port = runtime.charts.get(chart.chart_id);
    if (!(port instanceof FakeChartPort)) throw new Error('no fake chart');
    await waitFor(() => void expect(port.bars.length).toBeGreaterThan(0));
    const oldest = port.bars[0]?.timeMs;
    if (oldest === undefined) throw new Error('no first bar');
    act(() => {
      port.emit('visibleRangeChange', {
        fromMs: oldest,
        toMs: oldest + 60_000,
      });
    });
    await waitFor(
      () =>
        void expect(getCandles).toHaveBeenCalledWith(
          expect.objectContaining({toMs: oldest, limit: 500})
        )
    );
  });

  it('rounds new drawings to a tick size that arrives after the chart mounts', async () => {
    const runtime = createRuntime(composeFeatures, DEMO);
    await runtime.registry.activateAll();
    const chart = activeChart(runtime.workspace.get());
    const {venue, venueMarketId} = parseInstrumentId(chart.instrument_id);
    const market = runtime.clients.market;
    const real = await market.listInstruments({venue, q: venueMarketId});
    const listed = real.instruments.find(
      i => i.instrument_id === chart.instrument_id
    );
    if (!listed) throw new Error(`no DEMO ${chart.instrument_id}`);
    let release: (list: InstrumentList) => void = () => undefined;
    vi.spyOn(market, 'listInstruments').mockReturnValue(
      new Promise(resolve => {
        release = resolve;
      })
    );
    render(
      <RuntimeProvider runtime={runtime}>
        <ChartPane
          chart={chart}
          active
          runtime={runtime}
          onMenu={() => undefined}
        />
      </RuntimeProvider>
    );
    await waitFor(
      () =>
        void expect(runtime.drawing.controllers.has(chart.chart_id)).toBe(true)
    );
    const port = runtime.charts.get(chart.chart_id);
    if (!(port instanceof FakeChartPort)) throw new Error('no fake chart');

    // Instrument metadata arrives only now, after the controller was built.
    await act(async () => {
      release({...real, instruments: [{...listed, tick_size: '0.5'}]});
    });
    act(() => {
      runtime.drawing.controllers
        .get(chart.chart_id)
        ?.setTool('horizontal-line');
      // The fake chart maps y = 123 to price 175.4.
      port.emit('pointerDown', press(port, 100, 123));
      port.emit('pointerUp', press(port, 100, 123));
    });

    const drawings = runtime.drawing.stores.get(chart.chart_id)?.get().drawings;
    expect(drawings?.map(d => d.anchors[0]?.price)).toEqual(['175.5']);
  });
});
