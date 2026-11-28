/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview The DEMO heatmap tab: tiles, opening charts, legend
 * toggles, zooming into a sector, unavailable options and settings that
 * persist in the workspace.
 */

import {activeChart} from '@joseph/terminal-shell';
import {act, fireEvent, render, screen, within} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {createRuntime} from '../../composition/runtime';
import {Heatmap} from './heatmap-view';

/** DEMO configuration without engines. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: 1200,
    bottom: 700,
    width: 1200,
    height: 700,
    toJSON: () => ({}),
  });
});

afterEach(() => void vi.restoreAllMocks());

/**
 * Creates a DEMO runtime with an active heatmap tab and renders it.
 * @return Runtime and the chart id of the heatmap tab.
 */
function setup() {
  const runtime = createRuntime(() => [], DEMO);
  runtime.dispatch({
    type: 'addChart',
    chartId: 'chart-heat',
    viewType: 'heatmap',
  });
  const chart = activeChart(runtime.workspace.get());
  render(<Heatmap chart={chart} active runtime={runtime} />);
  return {runtime, chartId: chart.chart_id};
}

/**
 * Lists the visible tile buttons.
 * @return Tile buttons.
 */
function tiles(): HTMLElement[] {
  return within(screen.getByRole('group', {name: /tiles$/})).queryAllByRole(
    'button',
    {name: /,/}
  );
}

describe('DEMO heatmap', () => {
  it('settles on a fractional container width without an update loop', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1031.7,
      bottom: 600.4,
      width: 1031.7,
      height: 600.4,
      toJSON: () => ({}),
    });
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
    setup();
    await screen.findByRole('button', {name: /^ACME\b.*Acme/});
    expect(tiles().length).toBeGreaterThan(50);
    expect(
      errors.mock.calls.some(call =>
        /Maximum update depth/.test(String(call[0]))
      )
    ).toBe(false);
  });

  it('renders sector-grouped DEMO stocks and opens a chartable tile', async () => {
    const {runtime} = setup();
    const acme = await screen.findByRole('button', {name: /^ACME\b.*Acme/});
    expect(screen.getByText('DEMO')).toBeInTheDocument();
    expect(
      screen.getByRole('button', {name: 'Technology'})
    ).toBeInTheDocument();
    expect(tiles().length).toBeGreaterThan(50);
    fireEvent.click(acme);
    expect(activeChart(runtime.workspace.get())).toMatchObject({
      instrument_id: 'mock:stock:ACME',
      view_type: 'candles',
    });
    runtime.flush();
  });

  it('marks fictional tiles without a chart and keeps clicks inert', async () => {
    const {runtime} = setup();
    const tile = await screen.findByRole('button', {name: /\bORBQ\b/});
    expect(tile).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(tile);
    expect(activeChart(runtime.workspace.get()).view_type).toBe('heatmap');
    runtime.flush();
  });

  it('hides and restores tiles of a legend segment independently', async () => {
    const {runtime} = setup();
    await screen.findByRole('button', {name: /^ACME\b/});
    const before = tiles().length;
    const legend = screen.getByRole('group', {name: /Colour legend/});
    const [first, second] = within(legend).getAllByRole('button');
    if (!first || !second) throw new Error('legend has no segments');
    fireEvent.click(first);
    fireEvent.click(second);
    expect(first).toHaveAttribute('aria-pressed', 'false');
    const hiddenBoth = tiles().length;
    fireEvent.click(first);
    expect(first).toHaveAttribute('aria-pressed', 'true');
    expect(second).toHaveAttribute('aria-pressed', 'false');
    const hiddenOne = tiles().length;
    fireEvent.click(second);
    expect(tiles().length).toBe(before);
    expect(hiddenBoth).toBeLessThanOrEqual(hiddenOne);
    expect(hiddenOne).toBeLessThanOrEqual(before);
    runtime.flush();
  });

  it('zooms into a sector, persists it and goes back', async () => {
    const {runtime, chartId} = setup();
    fireEvent.click(await screen.findByRole('button', {name: 'Energy'}));
    expect(
      screen.getByRole('navigation', {name: 'Heatmap zoom'})
    ).toHaveTextContent('Energy');
    expect(tiles()).toHaveLength(4);
    const panels = runtime.workspace.get().panels as Record<
      string,
      {zoom?: string}
    >;
    expect(panels[`heatmap.${chartId}`]?.zoom).toBe('Energy');
    fireEvent.click(screen.getByRole('button', {name: 'All sectors'}));
    expect(tiles().length).toBeGreaterThan(50);
    runtime.flush();
  });

  it('disables unavailable options with their reason', async () => {
    const {runtime} = setup();
    await screen.findByRole('button', {name: /^ACME\b/});
    fireEvent.change(screen.getByLabelText('Data set'), {
      target: {value: 'us_etfs'},
    });
    const aum = await screen.findByRole('option', {
      name: 'Assets under management — unavailable',
    });
    expect(aum).toBeDisabled();
    expect(aum).toHaveAttribute('title', expect.stringMatching(/free sources/));
    expect(screen.getByLabelText('Group by')).toHaveValue('asset_class');
    fireEvent.change(screen.getByLabelText('Data set'), {
      target: {value: 'crypto_all'},
    });
    expect(
      await screen.findByRole('option', {name: 'Change 4h, % — unavailable'})
    ).toBeDisabled();
    expect(screen.queryByLabelText('Group by')).toBeNull();
    runtime.flush();
  });

  it('persists settings per tab across a reload', async () => {
    const {runtime, chartId} = setup();
    await screen.findByRole('button', {name: /^ACME\b/});
    fireEvent.change(screen.getByLabelText('Color by'), {
      target: {value: 'perf_1w'},
    });
    fireEvent.change(screen.getByLabelText('Size by'), {
      target: {value: 'mono'},
    });
    fireEvent.click(screen.getByRole('button', {name: 'Display settings'}));
    fireEvent.click(screen.getByRole('radio', {name: 'Symbol description'}));
    fireEvent.click(screen.getByRole('radio', {name: 'Color-blind'}));
    act(() => void runtime.flush());
    const restored = createRuntime(() => [], DEMO);
    const panels = restored.workspace.get().panels as Record<string, unknown>;
    expect(panels[`heatmap.${chartId}`]).toMatchObject({
      color: 'perf_1w',
      size: 'mono',
      title: 'description',
      scheme: 'colorblind',
    });
    expect(
      screen.getByRole('button', {name: /^Orbitron.*ORBQ/})
    ).toHaveTextContent(/Orbitron/);
  });

  it('offers chart, watchlist and zoom on right click', async () => {
    const {runtime} = setup();
    const execute = vi
      .spyOn(runtime.commands, 'execute')
      .mockResolvedValue(undefined);
    fireEvent.contextMenu(await screen.findByRole('button', {name: /^ACME\b/}));
    const menu = screen.getByRole('menu', {name: 'ACME actions'});
    fireEvent.click(
      within(menu).getByRole('menuitem', {name: 'Add to watchlist'})
    );
    expect(execute).toHaveBeenCalledWith('watchlist.add', {
      instrumentId: 'mock:stock:ACME',
    });
    fireEvent.contextMenu(screen.getByRole('button', {name: /\bORBQ\b/}));
    expect(screen.getByRole('menuitem', {name: 'Open chart'})).toBeDisabled();
    runtime.flush();
  });
});
