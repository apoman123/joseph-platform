/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for signal ranking (newest, R multiple, confluence,
 * age, distance to AOI) and local snooze/dismiss triage.
 */

import type {ReasonNode, Signal} from '@joseph/contracts';
import {fireEvent, screen, waitFor, within} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {ALERTS_FEATURE, triageFromSettings} from './index';
import {
  aoiDistancePct,
  confluenceOf,
  isSnoozed,
  setDismissed,
  snooze,
  snoozeLabel,
  sortSignals,
  type SignalTriage,
} from './ranking';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

/**
 * Builds a reason node.
 * @param result Result.
 * @param children Children.
 * @return Node.
 */
function node(
  result: ReasonNode['result'],
  children: ReasonNode[] = []
): ReasonNode {
  return {
    node_path: 'n',
    op: 'AND',
    detail: '',
    result,
    children,
  };
}

/**
 * Builds a partial signal with the fields ranking and triage read.
 * @param id Signal id.
 * @param extra Field overrides.
 * @return Signal; fields the helpers under test never read are absent.
 */
function signal(id: string, extra: Partial<Signal>): Signal {
  // A deliberate partial mock: the ranking helpers read only these fields.
  return {
    signal_id: id,
    bar_close_ms: 1000,
    evaluated_at_ms: 1000,
    rr: null,
    reason_tree: node('TRUE'),
    ...extra,
  } as unknown as Signal;
}

describe('signal ranking', () => {
  const a = signal('a', {
    bar_close_ms: 3000,
    rr: '1.5',
    reason_tree: node('TRUE', [node('TRUE'), node('FALSE')]),
  });
  const b = signal('b', {
    bar_close_ms: 2000,
    rr: '3.2',
    reason_tree: node('TRUE', [node('TRUE'), node('TRUE'), node('UNKNOWN')]),
  });
  const c = signal('c', {bar_close_ms: 1000, rr: null});

  it('counts satisfied leaves as confluence', () => {
    expect(confluenceOf(a.reason_tree)).toBe(1);
    expect(confluenceOf(b.reason_tree)).toBe(2);
  });

  it('sorts by every key with missing values last', () => {
    const ids = (list: Signal[]) => list.map(s => s.signal_id);
    expect(ids(sortSignals([c, b, a], 'newest'))).toEqual(['a', 'b', 'c']);
    expect(ids(sortSignals([a, b, c], 'rr'))).toEqual(['b', 'a', 'c']);
    expect(ids(sortSignals([a, b, c], 'confluence'))).toEqual(['b', 'a', 'c']);
    expect(ids(sortSignals([a, b, c], 'age'))).toEqual(['c', 'b', 'a']);
    expect(
      ids(sortSignals([a, b, c], 'distance', {a: 1.2, b: 0, c: null}))
    ).toEqual(['b', 'a', 'c']);
  });

  it('measures distance to the nearest AOI, zero inside', () => {
    expect(
      aoiDistancePct(100, [
        {lower: '98', upper: '99'},
        {lower: '103', upper: '104'},
      ])
    ).toBeCloseTo(1);
    expect(aoiDistancePct(100, [{lower: '99', upper: '101'}])).toBe(0);
    expect(aoiDistancePct(null, [{lower: '1', upper: '2'}])).toBeNull();
    expect(aoiDistancePct(100, [])).toBeNull();
  });

  it('snoozes until a time, expires old snoozes and dismisses', () => {
    let triage: SignalTriage = {dismissed: [], snoozed: {old: 5}};
    triage = snooze(triage, 'x', 15, 1000);
    expect(triage.snoozed).toEqual({x: 1000 + 15 * 60_000});
    expect(isSnoozed(triage, 'x', 2000)).toBe(true);
    expect(isSnoozed(triage, 'x', 1000 + 15 * 60_000)).toBe(false);
    expect(snoozeLabel(1000 + 90 * 60_000, 1000)).toBe('for 1.5 h');
    expect(snoozeLabel(1000 + 5 * 60_000, 1000)).toBe('for 5 min');
    triage = setDismissed(triage, 'y', true);
    expect(triage.dismissed).toEqual(['y']);
    expect(setDismissed(triage, 'y', false).dismissed).toEqual([]);
    expect(
      triageFromSettings({
        acked: [],
        dismissed: ['z', 3 as never],
        snoozed: {q: 'x' as never, r: 9},
      })
    ).toEqual({
      dismissed: ['z'],
      snoozed: {r: 9},
    });
  });
});

describe('alerts triage UI', () => {
  it('hides snoozed and dismissed signals, shows them on request and persists', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    const row = await screen.findByTestId('signal-demo-signal-1');
    fireEvent.change(within(row).getByTestId('signal-snooze'), {
      target: {value: '60'},
    });
    await waitFor(
      () => void expect(screen.queryByTestId('signal-demo-signal-1')).toBeNull()
    );
    fireEvent.click(
      within(screen.getByTestId('signal-demo-signal-2')).getByTestId(
        'signal-dismiss'
      )
    );
    await waitFor(
      () => void expect(screen.queryByTestId('signal-demo-signal-2')).toBeNull()
    );
    fireEvent.click(screen.getByTestId('alerts-show-hidden'));
    const restored = await screen.findByTestId('signal-demo-signal-2');
    expect(within(restored).getByTestId('signal-dismiss')).toHaveTextContent(
      'Restore'
    );
    expect(
      within(screen.getByTestId('signal-demo-signal-1')).getByRole('button', {
        name: 'Wake',
      })
    ).toBeInTheDocument();
    const saved = rendered.ctx.settings.read<{
      dismissed: string[];
      snoozed: Record<string, number>;
    }>({dismissed: [], snoozed: {}});
    expect(saved.dismissed).toEqual(['demo-signal-2']);
    expect(Object.keys(saved.snoozed)).toEqual(['demo-signal-1']);
  });

  it('re-sorts the table by R multiple', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    await screen.findByTestId('signal-demo-signal-1');
    fireEvent.change(screen.getByTestId('alerts-sort'), {
      target: {value: 'rr'},
    });
    const rows = screen.getAllByTestId(/^signal-demo-signal-/);
    const rr = rows.map(r => r.querySelectorAll('td')[5]?.textContent ?? '');
    const numeric = rr.filter(v => v !== '—').map(Number);
    expect(numeric).toEqual([...numeric].sort((x, y) => y - x));
  });
});
