/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Unit and property tests for the intent builder.
 */

import {Decimal} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  buildIntentRequest,
  defaultTicketInputs,
  describeAction,
  type TicketInputs,
} from './intent-builder';
import {quantityFromRisk, rewardToRisk} from './risk';
import {BTC} from './testing/fixtures';

/**
 * Builds ticket inputs for BTC with overrides.
 * @param patch Overrides.
 * @return Inputs.
 */
function inputs(patch: Partial<TicketInputs>): TicketInputs {
  return {
    ...defaultTicketInputs(BTC.instrument_id, 'acct-paper-demo'),
    ...patch,
  };
}

describe('buildIntentRequest', () => {
  it('floors quantity to the lot and rounds price to the tick', () => {
    const result = buildIntentRequest(
      inputs({quantity: '0.01099', limitPrice: '95000.04'}),
      BTC,
      null
    );
    expect(result.request?.quantity_base).toBe('0.01');
    expect(result.request?.limit_price).toBe('95000');
    expect(result.issues.filter(i => i.severity === 'warning')).toHaveLength(2);
  });

  it('blocks quantities below the minimum and zero after rounding', () => {
    const tiny = buildIntentRequest(
      inputs({quantity: '0.0004', limitPrice: '95000'}),
      BTC,
      null
    );
    expect(tiny.request).toBeNull();
    expect(
      tiny.issues.some(i => i.field === 'quantity' && i.severity === 'error')
    ).toBe(true);
  });

  it('enforces the minimum notional using the limit price', () => {
    const result = buildIntentRequest(
      inputs({quantity: '0.001', limitPrice: '1000'}),
      BTC,
      null
    );
    expect(result.issues.some(i => i.field === 'notional')).toBe(true);
  });

  it('requires prices for limit and stop orders', () => {
    expect(
      buildIntentRequest(inputs({quantity: '0.01'}), BTC, null).issues.some(
        i => i.field === 'limitPrice'
      )
    ).toBe(true);
    const stop = buildIntentRequest(
      inputs({quantity: '0.01', orderType: 'stop_market'}),
      BTC,
      null
    );
    expect(stop.issues.some(i => i.field === 'triggerPrice')).toBe(true);
  });

  it('rejects a stop-loss on the wrong side of entry', () => {
    const result = buildIntentRequest(
      inputs({quantity: '0.01', limitPrice: '95000', stopLoss: '96000'}),
      BTC,
      null
    );
    expect(result.request).toBeNull();
    expect(result.issues.find(i => i.field === 'stopLoss')?.message).toContain(
      'below'
    );
  });

  it('builds a bracket with exact decimal strings', () => {
    const result = buildIntentRequest(
      inputs({
        quantity: '0.010',
        limitPrice: '95000',
        stopLoss: '94500.06',
        takeProfit: '96000',
      }),
      BTC,
      null
    );
    expect(result.request?.bracket).toEqual({
      stop_loss: {trigger: '94500.1', basis: 'mark'},
      take_profit: [{trigger: '96000', fraction: '1'}],
    });
  });

  it('uses IOC for market orders and drops brackets when reduce-only', () => {
    const result = buildIntentRequest(
      inputs({
        quantity: '0.01',
        orderType: 'market',
        reduceOnly: true,
        stopLoss: '90000',
      }),
      BTC,
      Decimal.parse('95000')
    );
    expect(result.request?.time_in_force).toBe('IOC');
    expect(result.request?.bracket).toBeNull();
    expect(result.request?.position_effect).toBe('reduce');
  });

  it('blocks non-trading instruments and missing accounts', () => {
    const halted = buildIntentRequest(
      inputs({quantity: '0.01', limitPrice: '95000', accountId: null}),
      {...BTC, status: 'HALTED'},
      null
    );
    expect(halted.request).toBeNull();
    expect(halted.issues.map(i => i.field)).toEqual(
      expect.arrayContaining(['instrumentId', 'accountId'])
    );
  });

  it('never increases the typed quantity (property)', () => {
    let seed = 7;
    const next = () => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed;
    };
    for (let i = 0; i < 400; i += 1) {
      const typed = `${next() % 50}.${String(next() % 1000000).padStart(6, '0')}`;
      const result = buildIntentRequest(
        inputs({quantity: typed, limitPrice: '95000'}),
        BTC,
        null
      );
      if (result.quantity) {
        expect(result.quantity.cmp(Decimal.parse(typed))).toBeLessThanOrEqual(
          0
        );
        expect(result.quantity.isMultipleOf(Decimal.parse(BTC.lot_size))).toBe(
          true
        );
      }
    }
  });
});

describe('describeAction', () => {
  it('names the exact action', () => {
    const result = buildIntentRequest(
      inputs({quantity: '0.01', limitPrice: '95000'}),
      BTC,
      null
    );
    expect(result.request).not.toBeNull();
    if (!result.request) return;
    expect(describeAction(result.request, BTC)).toBe(
      'Place paper limit buy 0.010 BTC at 95,000.0'
    );
  });

  it('describes stop orders with their trigger', () => {
    const result = buildIntentRequest(
      inputs({
        quantity: '0.5',
        orderType: 'stop_market',
        side: 'sell',
        triggerPrice: '94000',
      }),
      BTC,
      null
    );
    if (!result.request) throw new Error('expected request');
    expect(describeAction(result.request, BTC)).toBe(
      'Place paper stop-market sell 0.500 BTC triggered at 94,000.0'
    );
  });
});

describe('risk helpers', () => {
  it('sizes from risk percent and floors to the lot', () => {
    const qty = quantityFromRisk({
      equity: Decimal.parse('100000'),
      riskPct: Decimal.parse('0.5'),
      entry: Decimal.parse('95000'),
      stop: Decimal.parse('94000'),
      lotSize: Decimal.parse('0.001'),
    });
    expect(qty?.toString()).toBe('0.5');
    expect(
      quantityFromRisk({
        equity: Decimal.parse('1000'),
        riskPct: Decimal.parse('1'),
        entry: Decimal.parse('100'),
        stop: Decimal.parse('100'),
        lotSize: Decimal.parse('1'),
      })
    ).toBeNull();
  });

  it('computes reward to risk per side', () => {
    expect(
      rewardToRisk(
        'buy',
        Decimal.parse('100'),
        Decimal.parse('95'),
        Decimal.parse('110')
      )?.toString()
    ).toBe('2');
    expect(
      rewardToRisk(
        'sell',
        Decimal.parse('100'),
        Decimal.parse('105'),
        Decimal.parse('85')
      )?.toString()
    ).toBe('3');
    expect(
      rewardToRisk(
        'buy',
        Decimal.parse('100'),
        Decimal.parse('105'),
        Decimal.parse('110')
      )
    ).toBeNull();
  });
});
