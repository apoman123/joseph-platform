/**
 * @fileoverview Integration tests of the engine-backed drawings in the
 * shell: the DEMO engine series flow through `DrawingSeriesSync` into the
 * object list, the data window and the style bar's tool inputs.
 */
import {FakeChartPort} from '@joseph/chart-core';
import {TIMEFRAME_MS, floorToTimeframe} from '@joseph/contracts';
import {
  DrawingController,
  DrawingSeriesSync,
  DrawingStore,
  createCommand,
  createDrawing,
  setDrawingFlags,
} from '@joseph/drawing-core';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import {describe, expect, it} from 'vitest';
import {composeFeatures} from '../composition/features';
import {createRuntime, type TerminalRuntime} from '../composition/runtime';
import {describeDrawing} from '../features/drawing/objects-panel';
import {DataWindow, drawingDataRows} from './data-window';
import {DrawingStyleBar} from './drawing-style-bar';
import {RuntimeProvider} from './runtime-context';

/** DEMO configuration. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

/** 5m bar duration. */
const BAR = TIMEFRAME_MS['5m'];

/** Palette used for new drawings. */
const PALETTE = {
  user: '#e66fd2',
  text: '#8fa5b3',
  bid: '#3cc79a',
  ask: '#f0645a',
  background: '#0e1c25',
  fontFamily: 'Archivo',
};

/**
 * Start of the bar `n` bars before the latest closed 5m bar.
 * @param n Bars back.
 * @return Bar start.
 */
function barsBack(n: number): number {
  return floorToTimeframe('5m', Date.now()) - (n + 1) * BAR;
}

/**
 * Mounts one engine-backed drawing on chart-1 of a DEMO runtime.
 * @param toolId Tool id.
 * @param times Anchor times.
 * @param settled Series status to wait for.
 * @return Runtime, store, drawing id and series sync.
 */
async function mount(
  toolId: string,
  times: number[],
  settled: 'ready' | 'error' = 'ready'
) {
  const runtime: TerminalRuntime = createRuntime(composeFeatures, DEMO);
  await runtime.registry.activateAll();
  const tool = runtime.tools.get(toolId);
  if (!tool) throw new Error(`missing ${toolId}`);
  const store = new DrawingStore();
  const doc = createDrawing({
    toolId,
    toolVersion: 1,
    instrumentId: 'mock:perp:BTCUSD',
    timeframe: null,
    anchors: times.map(time => ({time_ms: time, price: '95000'})),
    style: tool.defaultStyle(PALETTE),
    props: tool.defaultProps?.(),
    zIndex: 1,
    nowMs: 0,
    newId: () => 'd1',
  });
  store.execute(createCommand(doc));
  const series = new DrawingSeriesSync(store, runtime.tools, {
    fetch: query => runtime.clients.market.getIndicatorSeries(query),
    instrumentId: () => 'mock:perp:BTCUSD',
    timeframe: () => '5m',
    onChange: () => runtime.drawing.revision.update(n => n + 1),
    debounceMs: 0,
  });
  const controller = new DrawingController(
    new FakeChartPort(),
    store,
    runtime.tools,
    {
      instrumentId: () => 'mock:perp:BTCUSD',
      timeframe: () => '5m',
      tickSize: () => '0.1',
      bars: () => [],
      durationMs: () => BAR,
      magnet: () => false,
      palette: () => PALETTE,
      now: () => Date.now(),
      newId: () => 'x',
      series: d => series.get(d.drawing_id),
    }
  );
  runtime.drawing.stores.set('chart-1', store);
  runtime.drawing.controllers.set('chart-1', controller);
  runtime.drawing.series.set('chart-1', series);
  await waitFor(() => expect(series.get('d1')?.status).toBe(settled));
  return {runtime, store, series, id: doc.drawing_id};
}

describe('engine-backed drawings in the shell', () => {
  it('lists the engine VWAP and shows it in the data window', async () => {
    const anchor = barsBack(20);
    const {runtime} = await mount('anchored-vwap', [anchor + 1000]);
    const doc = runtime.drawing.stores.get('chart-1')?.find('d1');
    if (!doc) throw new Error('no drawing');
    expect(describeDrawing(runtime, doc, 'chart-1').detail).toMatch(
      /^VWAP [\d,]+\.\d\d from \d{4}-\d\d-\d\d \d\d:\d\d UTC · bands 1σ$/
    );
    const rows = drawingDataRows(runtime, 'chart-1', anchor + BAR, '0.1', null);
    expect(rows.map(r => r.label)).toEqual([
      'Anchored VWAP',
      'VWAP +1σ',
      'VWAP −1σ',
    ]);
    expect(rows[0]?.value).toMatch(/^[\d,]+\.\d$/);
    // Replay hides bars that closed after the cursor.
    expect(
      drawingDataRows(runtime, 'chart-1', anchor + BAR, '0.1', anchor + BAR)
    ).toEqual([]);
    render(
      <DataWindow
        bar={null}
        timeframe="5m"
        previousClose={null}
        tickSize="0.1"
        delta={null}
        compare={[]}
        label={id => id}
        drawingRows={rows}
      />
    );
    expect(screen.getByTestId('data-window')).toHaveTextContent(
      'Anchored VWAP'
    );
  });

  it('draws anchors 2400 bars back (the engine computes up to 50 000)', async () => {
    const {runtime} = await mount('anchored-vwap', [barsBack(2400)]);
    const doc = runtime.drawing.stores.get('chart-1')?.find('d1');
    if (!doc) throw new Error('no drawing');
    expect(describeDrawing(runtime, doc, 'chart-1').detail).toMatch(/^VWAP \d/);
    expect(
      drawingDataRows(runtime, 'chart-1', barsBack(1), null, null)[0]
    ).toMatchObject({key: 'd1:0', label: 'Anchored VWAP'});
  });

  it('shows the engine message only when the engine refuses the anchor', async () => {
    const {runtime} = await mount('anchored-vwap', [barsBack(50_001)], 'error');
    const doc = runtime.drawing.stores.get('chart-1')?.find('d1');
    if (!doc) throw new Error('no drawing');
    expect(describeDrawing(runtime, doc, 'chart-1').detail).toMatch(
      /^Anchor is more than 50,000 bars back/
    );
    expect(
      drawingDataRows(runtime, 'chart-1', barsBack(1), null, null)
    ).toEqual([
      {key: 'd1:0', label: 'Anchored VWAP', value: 'refused by the engine'},
    ]);
  });

  it('summarizes the regression channel at the t2 bar', async () => {
    const {runtime} = await mount('regression-channel', [
      barsBack(40),
      barsBack(10),
    ]);
    const doc = runtime.drawing.stores.get('chart-1')?.find('d1');
    if (!doc) throw new Error('no drawing');
    expect(describeDrawing(runtime, doc, 'chart-1').detail).toMatch(
      /^R -?\d\.\d{3} · end [\d,]+\.\d\d · 31 bars$/
    );
    expect(
      drawingDataRows(runtime, 'chart-1', barsBack(5), '0.1', null).map(
        r => r.label
      )
    ).toEqual([
      'Regression end',
      'Channel upper',
      'Channel lower',
      "Pearson's R",
    ]);
  });

  it('edits tool inputs from the style bar and re-requests the series', async () => {
    const {runtime, store, series, id} = await mount('anchored-vwap', [
      barsBack(12),
    ]);
    runtime.drawing.selection.set({chartId: 'chart-1', drawingId: id});
    render(
      <RuntimeProvider runtime={runtime}>
        <DrawingStyleBar chartId="chart-1" />
      </RuntimeProvider>
    );
    const inputs = screen.getByTestId('drawing-inputs');
    expect(inputs).toHaveTextContent('Band 1');
    fireEvent.change(screen.getByLabelText('Band 2'), {
      target: {value: '2'},
    });
    expect(store.find(id)?.props).toMatchObject({band_1: '1', band_2: '2'});
    expect(series.get(id)?.request.settings.band_2).toBe('2');
    await waitFor(() => expect(series.get(id)?.status).toBe('ready'));
    store.undo();
    expect(store.find(id)?.props).toMatchObject({band_2: '0'});
    setDrawingFlags(store, id, {locked: true}, Date.now());
    runtime.drawing.revision.update(n => n + 1);
    await waitFor(() => expect(screen.getByLabelText('Band 2')).toBeDisabled());
  });
});
