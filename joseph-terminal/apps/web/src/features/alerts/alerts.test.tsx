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
  it('lists signals and opens the reason tree with tri-state results', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    const row = await screen.findByTestId('signal-demo-signal-2');
    await userEvent
      .setup()
      .click(within(row).getByRole('button', {name: 'Entry blocked'}));
    const tree = screen.getByRole('list', {name: 'Why this signal'});
    expect(tree.querySelector('[data-result="UNKNOWN"]')).not.toBeNull();
    expect(
      within(tree).getByText('Open interest snapshot missing')
    ).toBeInTheDocument();
  });

  it('focuses the exact instrument, bar and AOI on the chart', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    const row = await screen.findByTestId('signal-demo-signal-1');
    await userEvent
      .setup()
      .click(within(row).getByRole('button', {name: 'Show on chart'}));
    const focus = rendered.ctx.emitted.find(e => e.type === 'signal.focus');
    expect(focus?.payload).toMatchObject({
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      aoiIds: ['demo-fvg-1'],
    });
    expect(rendered.ctx.selection.get().instrumentId).toBe('mock:perp:BTCUSD');
  });

  it('filters by kind and hides acknowledged signals', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    const user = userEvent.setup();
    await screen.findByTestId('signal-demo-signal-1');
    await user.click(screen.getByRole('button', {name: 'Blocked'}));
    expect(
      screen.queryByTestId('signal-demo-signal-1')
    ).not.toBeInTheDocument();
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
    await waitFor(() =>
      expect(screen.getByTestId('signal-streamed-1')).toBeInTheDocument()
    );
  });

  it('keeps the highest revision when merging', () => {
    // A deliberate partial signal: merging reads only the id, revision and
    // the two sort times.
    const a = {
      signal_id: 's',
      revision: 1,
      bar_close_ms: 1,
      evaluated_at_ms: 1,
    } as unknown as Signal;
    const b = {...a, revision: 0};
    expect(mergeSignals([a], [b])[0]?.revision).toBe(1);
    expect(mergeSignals([b], [a])[0]?.revision).toBe(1);
  });
});
