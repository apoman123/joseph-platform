/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Component tests for the alerts feature.
 */

import type {Signal} from '@joseph/contracts';
import {act, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it} from 'vitest';
import {
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {ALERTS_FEATURE, mergeSignals} from './index';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

describe('alerts', () => {
  it('lists signals and opens their message and levels', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    const user = userEvent.setup();
    const exit = await screen.findByTestId('signal-demo-signal-2');
    await user.click(
      within(exit).getByRole('button', {name: 'Position exited'})
    );
    const exitDetails = screen.getByLabelText('Signal details');
    expect(exitDetails).toHaveTextContent('RSI back below 50');
    expect(exitDetails).not.toHaveTextContent('Levels');
    const entry = screen.getByTestId('signal-demo-signal-1');
    await user.click(within(entry).getByRole('button', {name: 'Entry'}));
    const details = screen.getByLabelText('Signal details');
    expect(details).toHaveTextContent('Entry 94790, stop 94650, target 95100');
    expect(details).toHaveTextContent('paper');
  });

  it('focuses the exact instrument and bar on the chart', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    const row = await screen.findByTestId('signal-demo-signal-1');
    await userEvent
      .setup()
      .click(within(row).getByRole('button', {name: 'Show on chart'}));
    const focus = rendered.ctx.emitted.find(e => e.type === 'signal.focus');
    expect(focus?.payload).toEqual({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      timeMs: expect.any(Number),
    });
    expect(rendered.ctx.selection.get().instrumentId).toBe('mock:perp:BTCUSD');
  });

  it('filters by kind and hides acknowledged signals', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    const user = userEvent.setup();
    await screen.findByTestId('signal-demo-signal-1');
    await user.click(screen.getByRole('button', {name: 'Exits'}));
    expect(
      screen.queryByTestId('signal-demo-signal-1')
    ).not.toBeInTheDocument();
    expect(screen.getByTestId('signal-demo-signal-2')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'All'}));
    await user.click(
      within(screen.getByTestId('signal-demo-signal-3')).getByRole('button', {
        name: 'Acknowledge',
      })
    );
    expect(
      screen.queryByTestId('signal-demo-signal-3')
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole('checkbox', {name: 'Show acknowledged'}));
    expect(screen.getByTestId('signal-demo-signal-3')).toBeInTheDocument();
  });

  it('shows streamed signals immediately', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    await screen.findByTestId('signal-demo-signal-1');
    const base = (await rendered.ctx.strategy.listSignals({}))
      .signals[0] as Signal;
    act(() =>
      rendered?.ctx.strategy.emitSignal({
        ...base,
        signal_id: 'streamed-1',
        bar_close_ms: base.bar_close_ms + 300_000,
      })
    );
    await waitFor(
      () =>
        void expect(screen.getByTestId('signal-streamed-1')).toBeInTheDocument()
    );
  });

  it('merges signals by id, newest bar first', () => {
    // A deliberate partial signal: merging reads only the id and the two
    // sort times.
    const a = {
      signal_id: 'a',
      bar_close_ms: 1,
      evaluated_at_ms: 1,
    } as unknown as Signal;
    const b = {...a, signal_id: 'b', bar_close_ms: 2};
    const merged = mergeSignals([a], [b, {...a}]);
    expect(merged.map(s => s.signal_id)).toEqual(['b', 'a']);
  });
});
