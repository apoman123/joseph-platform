/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Signal ranking by the signal bar's quote turnover: nulls
 * last, compact display and the explanatory tooltip.
 */

import type {Signal} from '@joseph/contracts';
import {fireEvent, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {formatCompact} from '../../shell/format';
import {
  renderFeature,
  type RenderedFeature,
} from '../trading/testing/fake-context';
import {ALERTS_FEATURE} from './index';
import {
  BAR_VOLUME_HELP,
  SIGNAL_SORTS,
  barQuoteVolumeOf,
  sortSignals,
} from './ranking';

let rendered: RenderedFeature | null = null;

afterEach(() => {
  rendered?.dispose();
  rendered = null;
});

/**
 * Builds a minimal signal.
 * @param id Id.
 * @param closeMs Bar close.
 * @param quote Quote turnover (decimal string or null).
 * @return Signal.
 */
function signal(id: string, closeMs: number, quote: string | null): Signal {
  return {
    signal_id: id,
    bar_close_ms: closeMs,
    evaluated_at_ms: closeMs,
    bar_quote_volume: quote,
    reason_tree: {
      node_path: '/',
      op: 'ALL',
      detail: '',
      result: 'TRUE',
      children: [],
    },
  } as unknown as Signal;
}

describe('bar volume ranking', () => {
  it('sorts by quote turnover, largest first, missing values last', () => {
    const sorted = sortSignals(
      [
        signal('none', 9000, null),
        signal('small', 1000, '950.5'),
        signal('big', 2000, '1250000'),
        signal('bad', 8000, 'n/a'),
        signal('mid', 3000, '48210.75'),
      ],
      'volume'
    );
    expect(sorted.map(s => s.signal_id)).toEqual([
      'big',
      'mid',
      'small',
      'none',
      'bad',
    ]);
    expect(SIGNAL_SORTS.find(s => s.id === 'volume')?.label).toBe('Bar volume');
  });

  it('reads only valid, non-negative turnover', () => {
    expect(barQuoteVolumeOf(signal('a', 1, '12.5'))).toBe(12.5);
    expect(barQuoteVolumeOf(signal('a', 1, null))).toBeNull();
    expect(barQuoteVolumeOf(signal('a', 1, '-3'))).toBeNull();
  });

  it('formats volumes compactly', () => {
    expect(formatCompact('1234567')).toBe('1.2M');
    expect(formatCompact('999960')).toBe('1.0M');
    expect(formatCompact('48210.75')).toBe('48.2K');
    expect(formatCompact('950')).toBe('950');
    expect(formatCompact('3400000000')).toBe('3.4B');
    expect(formatCompact(null)).toBe('—');
  });
});

describe('bar volume column', () => {
  it('shows compact turnover with a tooltip and re-sorts by it', async () => {
    rendered = await renderFeature(ALERTS_FEATURE, 'bottom-panel');
    await screen.findByTestId('signal-demo-signal-1');
    expect(
      screen.getByRole('columnheader', {name: 'Bar vol.'})
    ).toHaveAttribute('title', BAR_VOLUME_HELP);
    fireEvent.change(screen.getByTestId('alerts-sort'), {
      target: {value: 'volume'},
    });
    const rows = screen.getAllByTestId(/^signal-demo-signal-/);
    const cells = rows.map(row => within(row).getByTestId('signal-volume'));
    for (const cell of cells) {
      expect(cell.textContent).toMatch(/^\d+(\.\d)?[KMB]?$/);
      expect(cell).toHaveAttribute(
        'title',
        expect.stringMatching(/Quote turnover of the signal bar/)
      );
    }
    const order = rows.map(row => row.getAttribute('data-testid'));
    const {signals} = await (rendered?.ctx.strategy.listSignals({}) ??
      Promise.resolve({signals: []}));
    const expected = sortSignals(signals, 'volume').map(
      s => `signal-${s.signal_id}`
    );
    expect(order).toEqual(expected);
  });
});
