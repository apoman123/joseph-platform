/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for context menu, cancel, close, reverse and kill
 * switch flows.
 */

import {Decimal} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  buildContextMenu,
  cancelGuard,
  planClose,
  planKillSwitch,
  reverseCloseSubmitted,
  reverseOpenTicket,
  reversePositionUpdated,
  startReverse,
  ticketFromMenuItem,
} from './flows';
import {defaultTicketInputs} from './intent-builder';
import {order, position} from './testing/fixtures';

describe('buildContextMenu', () => {
  it('rounds the price and labels every action', () => {
    const items = buildContextMenu(94980.04, '0.1', Decimal.parse('95000'));
    expect(items.map(i => i.label)).toEqual([
      'Buy limit at 94,980.0',
      'Sell limit at 94,980.0',
      'Buy stop at 94,980.0',
      'Sell stop at 94,980.0',
    ]);
    expect(items.find(i => i.id === 'sell-limit')?.hint).toMatch(
      /fills immediately/
    );
    expect(items.find(i => i.id === 'buy-stop')?.hint).toMatch(
      /trigger immediately/
    );
    expect(items.find(i => i.id === 'buy-limit')?.hint).toBeNull();
  });

  it('prefills the ticket from a menu item', () => {
    const [item] = buildContextMenu(100, '0.1', null);
    if (!item) throw new Error('expected item');
    const ticket = ticketFromMenuItem(
      item,
      defaultTicketInputs('mock:perp:BTCUSD', 'a')
    );
    expect(ticket).toMatchObject({
      side: 'buy',
      orderType: 'limit',
      limitPrice: '100',
      source: 'CHART',
    });
  });

  it('returns nothing for invalid prices', () => {
    expect(buildContextMenu(Number.NaN, '0.1', null)).toEqual([]);
  });
});

describe('cancelGuard', () => {
  it('blocks cancel while the outcome is unknown and says it is not canceled', () => {
    const guard = cancelGuard(order({state: 'UNKNOWN_RECONCILING'}));
    expect(guard.allowed).toBe(false);
    expect(guard.reason).toMatch(/not been canceled/);
  });

  it('allows cancel for working orders only', () => {
    expect(cancelGuard(order()).allowed).toBe(true);
    expect(cancelGuard(order({state: 'FILLED'})).allowed).toBe(false);
    expect(cancelGuard(order({state: 'SUBMITTING'})).allowed).toBe(false);
  });
});

describe('planClose', () => {
  it('floors the closing quantity and never flips', () => {
    const plan = planClose(
      position({quantity_base: '0.025'}),
      '0.5',
      'market',
      '0.001',
      'BTC'
    );
    if ('error' in plan) throw new Error(plan.error);
    expect(plan.quantity.toString()).toBe('0.012');
    expect(plan.summary).toBe(
      'Close 50% of long 0.025 BTC with a reduce-only market sell'
    );
    expect(plan.request.fraction).toBe('0.5');
  });

  it('rejects invalid fractions', () => {
    expect(
      planClose(position(), '1.5', 'market', '0.001', 'BTC')
    ).toHaveProperty('error');
    expect(
      planClose(position({side: 'flat'}), '1', 'market', '0.001', 'BTC')
    ).toHaveProperty('error');
    expect(planClose(position(), '1', 'limit', '0.001', 'BTC')).toHaveProperty(
      'error'
    );
  });
});

describe('reverse', () => {
  it('opens the opposite side only after a confirmed flat position', () => {
    const started = startReverse(position());
    if ('error' in started) throw new Error(started.error);
    expect(started.step).toBe('CLOSE');
    const waiting = reverseCloseSubmitted(started);
    expect(waiting.step).toBe('AWAITING_FLAT');
    expect(reversePositionUpdated(waiting, position()).step).toBe(
      'AWAITING_FLAT'
    );
    const open = reversePositionUpdated(
      waiting,
      position({side: 'flat', quantity_base: '0'})
    );
    expect(open.step).toBe('OPEN');
    expect(reverseOpenTicket(open, 'acct')).toMatchObject({
      side: 'sell',
      orderType: 'market',
      quantity: '0.02',
    });
  });
});

describe('planKillSwitch', () => {
  it('keeps cancel and flatten as separate explicit choices', () => {
    const plan = planKillSwitch({
      scope: 'GLOBAL',
      targetId: null,
      engage: true,
      cancelOpenOrders: false,
      flattenPositions: false,
      reason: 'news',
    });
    if ('error' in plan) throw new Error(plan.error);
    expect(plan.request).toMatchObject({
      cancel_open_orders: false,
      flatten_positions: false,
      target_id: null,
    });
    expect(plan.consequences).toContain('Open orders stay working.');
    expect(plan.consequences).toContain(
      'Risk-reducing orders (reduce-only closes) stay allowed.'
    );
  });

  it('requires a reason and a target for scoped switches', () => {
    expect(
      planKillSwitch({
        scope: 'GLOBAL',
        targetId: null,
        engage: true,
        cancelOpenOrders: true,
        flattenPositions: true,
        reason: ' ',
      })
    ).toHaveProperty('error');
    expect(
      planKillSwitch({
        scope: 'ACCOUNT',
        targetId: null,
        engage: true,
        cancelOpenOrders: false,
        flattenPositions: false,
        reason: 'x',
      })
    ).toHaveProperty('error');
  });

  it('never cancels or flattens on release', () => {
    const plan = planKillSwitch({
      scope: 'ACCOUNT',
      targetId: 'a',
      engage: false,
      cancelOpenOrders: true,
      flattenPositions: true,
      reason: 'resume',
    });
    if ('error' in plan) throw new Error(plan.error);
    expect(plan.request.cancel_open_orders).toBe(false);
    expect(plan.request.flatten_positions).toBe(false);
  });
});
