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
import {parseInstrumentId, type InstrumentList} from '@joseph/contracts';
import {activeChart} from '@joseph/terminal-shell';
import {act, render, waitFor} from '@testing-library/react';
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
