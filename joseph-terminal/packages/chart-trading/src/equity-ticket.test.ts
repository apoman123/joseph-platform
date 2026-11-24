/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Unit tests for cash-equity order intents against the
 * execution contract: board and odd lots (limit DAY only), tick-ladder
 * rounding, daily price limits, time in force and order types from the
 * account's capabilities, `outside_rth`, closed-market rules, long-only cash
 * accounts, routing, rates, confirm labels and the controller's context.
 */

import {
  Decimal,
  createBarCalendar,
  dayNumberOf,
  zonedTimeToUtc,
  type ExecutionClient,
  type OrderIntentRequest,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  buildEquityIntentRequest,
  buildIntentRequest,
  defaultTicketInputs,
  describeAction,
  type TicketInputs,
} from './intent-builder';
import {TicketController, modeAvailability} from './ticket';
import {
  TPEX_BOND_ETF,
  TWSE_STOCK,
  US_STOCK,
  account,
  brokerAccount,
  position,
} from './testing/fixtures';

const SINOPAC = brokerAccount('sinopac');
const IBKR = brokerAccount('ibkr');
const REF = Decimal.parse('585');
const XTAI = createBarCalendar('XTAI');
const XNYS = createBarCalendar('XNYS');
/** Monday 2026-11-30 10:00 Taipei (TWSE open). */
const TPE_OPEN = zonedTimeToUtc(dayNumberOf('2026-11-30'), 600, 'Asia/Taipei');
/** Saturday 2026-11-28 12:00 New York (closed). */
const NY_CLOSED = zonedTimeToUtc(
  dayNumberOf('2026-11-28'),
  720,
  'America/New_York'
);

/**
 * Builds default ticket inputs with DAY time in force for an instrument.
 * @param instrumentId Instrument id.
 * @param accountId Account id.
 * @param patch Field overrides.
 * @return Ticket inputs.
 */
function inputs(
  instrumentId: string,
  accountId: string,
  patch: Partial<TicketInputs>
): TicketInputs {
  return {
    ...defaultTicketInputs(instrumentId, accountId),
    timeInForce: 'DAY',
    ...patch,
  };
}

/**
 * Returns the issue messages of a build result.
 * @param result Build result.
 * @return Messages in issue order.
 */
function messages(result: ReturnType<typeof buildIntentRequest>): string[] {
  return result.issues.map(issue => issue.message);
}

describe('TPEx orders', () => {
  it('routes a TPEx bond ETF to SinoPac in board lots without a limit', () => {
    const ref = Decimal.parse('26.15');
    const result = buildIntentRequest(
      inputs(TPEX_BOND_ETF.instrument_id, SINOPAC.account_id, {
        quantity: '3',
        limitPrice: '30',
      }),
      TPEX_BOND_ETF,
      ref,
      {account: SINOPAC, reference: ref, calendar: XTAI, nowMs: TPE_OPEN}
    );
    // Foreign-constituent funds have no daily limit: 30 is 14.7 % up.
    expect(result.issues).toEqual([]);
    expect(result.request).toMatchObject({
      instrument_id: 'tpex:etf:00679B',
      quantity_base: '3000',
      limit_price: '30',
      time_in_force: 'DAY',
    });
    const offLadder = buildIntentRequest(
      inputs(TPEX_BOND_ETF.instrument_id, SINOPAC.account_id, {
        quantity: '1',
        limitPrice: '57.72',
      }),
      TPEX_BOND_ETF,
      ref,
      {account: SINOPAC, reference: ref, calendar: XTAI, nowMs: TPE_OPEN}
    );
    expect(offLadder.request?.limit_price).toBe('57.7');
    expect(messages(offLadder).join(' ')).toMatch(
      /Rounded to the tick ladder \(0\.05 at this price\)/
    );
  });
});

describe('TWSE orders', () => {
  it('sends board lots as shares with DAY and no leverage', () => {
    const result = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '2',
        limitPrice: '585',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC, reference: REF, calendar: XTAI, nowMs: TPE_OPEN}
    );
    expect(result.request).toMatchObject({
      quantity_base: '2000',
      limit_price: '585',
      time_in_force: 'DAY',
      leverage: null,
      reduce_only: false,
      bracket: null,
    });
    // Odd versus board lot is implied by the quantity; nothing else is sent.
    expect(result.request).not.toHaveProperty('outside_rth');
    expect(result.request).not.toHaveProperty('lot_type');
  });

  it('accepts limit DAY odd lots and refuses other odd-lot orders', () => {
    const odd = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        lotMode: 'odd',
        quantity: '350',
        limitPrice: '585',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC, reference: REF}
    );
    expect(odd.request?.quantity_base).toBe('350');
    const market = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        lotMode: 'odd',
        quantity: '350',
        orderType: 'market',
        timeInForce: 'IOC',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC}
    );
    expect(messages(market)).toEqual(
      expect.arrayContaining([
        'Intraday odd-lot orders are limit orders only.',
        'Intraday odd-lot orders are DAY (ROD) only.',
      ])
    );
    const tooMany = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        lotMode: 'odd',
        quantity: '1200',
        limitPrice: '585',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC}
    );
    expect(messages(tooMany).join(' ')).toMatch(/odd-lot order is 1 to 999/);
    const noOdd = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, 'acct-cathay', {
        lotMode: 'odd',
        quantity: '10',
        limitPrice: '585',
      }),
      TWSE_STOCK,
      REF,
      {account: brokerAccount('cathay', {}, {odd_lot: false})}
    );
    expect(messages(noOdd)).toContain(
      'This account cannot send intraday odd-lot orders.'
    );
  });

  it('rounds to the tick ladder and enforces the daily price limit', () => {
    const rounded = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '1',
        limitPrice: '585.4',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC, reference: REF}
    );
    expect(rounded.request?.limit_price).toBe('585');
    expect(messages(rounded)).toContain(
      'Rounded to the tick ladder (1 at this price): 585 TWD.'
    );
    const outside = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '1',
        limitPrice: '650',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC, reference: REF}
    );
    expect(outside.request).toBeNull();
    expect(messages(outside)).toContain(
      "Limit price 650 TWD is outside today's price limit 527 TWD – 643 TWD (±10% of the 585 TWD reference close)."
    );
  });

  it('refuses order types and time in force the account lacks', () => {
    const gtc = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '1',
        limitPrice: '585',
        timeInForce: 'GTC',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC}
    );
    expect(messages(gtc)).toContain(
      'This account accepts DAY, IOC, FOK for stocks and ETFs.'
    );
    const stop = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '1',
        orderType: 'stop_market',
        triggerPrice: '600',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC}
    );
    expect(messages(stop)).toContain(
      'This account does not offer stop-market orders.'
    );
    const none = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, 'acct-cathay', {
        quantity: '1',
        limitPrice: '585',
      }),
      TWSE_STOCK,
      REF,
      {
        account: brokerAccount(
          'cathay',
          {},
          {order_types: [], time_in_force: []}
        ),
      }
    );
    expect(messages(none)).toEqual(
      expect.arrayContaining([
        'This account does not offer limit orders.',
        'This account lists no time in force for stocks and ETFs.',
      ])
    );
  });

  it('labels the confirm button with lots, shares and currency', () => {
    const result = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '2',
        limitPrice: '585',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC}
    );
    if (!result.request) throw new Error('expected a request');
    expect(describeAction(result.request, TWSE_STOCK)).toBe(
      'Place paper limit buy 2 張 (2,000 shares) 9901 at 585 TWD'
    );
  });
});

describe('closed markets and cash accounts', () => {
  it('blocks market orders while closed unless the account queues DAY', () => {
    const base = inputs(US_STOCK.instrument_id, IBKR.account_id, {
      quantity: '100',
      orderType: 'market',
    });
    const queued = buildIntentRequest(base, US_STOCK, Decimal.parse('182.4'), {
      account: IBKR,
      calendar: XNYS,
      nowMs: NY_CLOSED,
    });
    expect(queued.request?.time_in_force).toBe('DAY');
    const gtc = buildIntentRequest(
      {...base, timeInForce: 'GTC'},
      US_STOCK,
      Decimal.parse('182.4'),
      {account: IBKR, calendar: XNYS, nowMs: NY_CLOSED}
    );
    expect(messages(gtc).join(' ')).toMatch(
      /send the market order as DAY to queue it/
    );
    const noQueue = buildIntentRequest(base, US_STOCK, Decimal.parse('182.4'), {
      account: brokerAccount('ibkr', {}, {queue_market_when_closed: false}),
      calendar: XNYS,
      nowMs: NY_CLOSED,
    });
    expect(messages(noQueue).join(' ')).toMatch(
      /market orders are rejected until the next open \(Mon 30 Nov 09:30 New York\)\. Use a limit order\./
    );
  });

  it('refuses short sales on long-only cash accounts', () => {
    const holding = brokerAccount('ibkr', {
      positions: [
        position({instrument_id: 'mock:stock:ACME', quantity_base: '50'}),
      ],
    });
    const sell = inputs(US_STOCK.instrument_id, IBKR.account_id, {
      side: 'sell',
      quantity: '100',
      limitPrice: '182.4',
    });
    expect(
      messages(buildIntentRequest(sell, US_STOCK, null, {account: holding}))
    ).toContain('This cash account cannot sell short: it holds 50 shares.');
    expect(
      messages(buildIntentRequest(sell, US_STOCK, null, {account: IBKR}))
    ).toContain('This cash account cannot sell short: it holds no shares.');
    expect(
      buildIntentRequest({...sell, quantity: '50'}, US_STOCK, null, {
        account: holding,
      }).request?.side
    ).toBe('sell');
    expect(
      buildIntentRequest(sell, US_STOCK, null, {
        account: brokerAccount('ibkr', {}, {short_selling: true}),
      }).request
    ).not.toBeNull();
  });
});

describe('US orders through IBKR', () => {
  it('sends outside_rth only when set and accepted', () => {
    const base = inputs(US_STOCK.instrument_id, IBKR.account_id, {
      quantity: '100',
      limitPrice: '182.4',
      outsideRth: true,
    });
    const supported = buildIntentRequest(base, US_STOCK, null, {account: IBKR});
    expect(supported.request).toMatchObject({
      quantity_base: '100',
      limit_price: '182.4',
      outside_rth: true,
    });
    const refused = buildIntentRequest(base, US_STOCK, null, {
      account: brokerAccount('ibkr', {}, {outside_rth: false}),
    });
    expect(refused.request).toBeNull();
    expect(messages(refused)).toContain(
      'This account does not accept outside-RTH orders.'
    );
    const market = buildIntentRequest(
      {...base, orderType: 'market'},
      US_STOCK,
      Decimal.parse('182.4'),
      {account: IBKR}
    );
    expect(messages(market)).toContain(
      'Outside regular hours applies to limit and stop-limit orders.'
    );
  });

  it('describes US share orders', () => {
    const result = buildIntentRequest(
      inputs(US_STOCK.instrument_id, IBKR.account_id, {
        quantity: '100',
        orderType: 'stop_market',
        triggerPrice: '190',
      }),
      US_STOCK,
      null,
      {account: IBKR}
    );
    if (!result.request) throw new Error('expected a request');
    expect(result.request.trigger_basis).toBe('last');
    expect(describeAction(result.request, US_STOCK)).toBe(
      'Place paper stop-market buy 100 shares ACME triggered at 190.00 USD'
    );
  });

  it('flags the wrong account, missing inputs and halted instruments', () => {
    const wrong = buildEquityIntentRequest(
      inputs(US_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '10',
        limitPrice: '182.4',
      }),
      US_STOCK,
      null,
      {account: SINOPAC}
    );
    expect(messages(wrong)).toContain(
      'SinoPac Securities (Shioaji) accounts route TWSE and TPEx listings only.'
    );
    const empty = buildEquityIntentRequest(
      inputs(US_STOCK.instrument_id, '', {accountId: null}),
      {...US_STOCK, status: 'HALTED'},
      null
    );
    expect(messages(empty)).toEqual(
      expect.arrayContaining([
        'This instrument is halted at the exchange; new orders are blocked.',
        'Choose a broker account.',
        'Enter a quantity.',
        'Enter a limit price.',
      ])
    );
    expect(
      messages(
        buildEquityIntentRequest(
          inputs(US_STOCK.instrument_id, IBKR.account_id, {
            quantity: '5000000',
            limitPrice: '-1',
          }),
          {...US_STOCK, min_notional: '10'},
          null,
          {account: IBKR}
        )
      )
    ).toEqual(
      expect.arrayContaining([
        'Maximum quantity is 1000000 shares.',
        'Limit price must be a positive number.',
      ])
    );
    expect(
      messages(
        buildEquityIntentRequest(
          inputs(US_STOCK.instrument_id, 'a', {}),
          null,
          null
        )
      )
    ).toEqual(['Instrument details are still loading.']);
  });

  it('checks the minimum order value when the venue has one', () => {
    const result = buildIntentRequest(
      inputs(US_STOCK.instrument_id, IBKR.account_id, {
        quantity: '1',
        limitPrice: '5',
      }),
      {...US_STOCK, min_notional: '10'},
      null,
      {account: IBKR}
    );
    expect(messages(result).join(' ')).toMatch(/below the minimum of 10 USD/);
  });
});

describe('rates and the controller', () => {
  it('never builds an order for a published rate', () => {
    const result = buildIntentRequest(
      defaultTicketInputs('mock:rate:UST10Y', 'acct-paper-demo'),
      null,
      null
    );
    expect(result.request).toBeNull();
    expect(messages(result)).toEqual([
      'Rates are published values and cannot be traded.',
    ]);
  });

  it('previews with the account and reference set as context', async () => {
    const seen: OrderIntentRequest[] = [];
    const previewOnly: Pick<ExecutionClient, 'previewOrder'> = {
      previewOrder: async (request: OrderIntentRequest) => {
        seen.push(request);
        throw new Error('not reached');
      },
    };
    const controller = new TicketController(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '1',
        limitPrice: '650',
      }),
      // Deliberate partial mock: this test can reach only previewOrder.
      {client: previewOnly as ExecutionClient}
    );
    controller.setMarket(TWSE_STOCK, REF);
    expect(controller.build().request?.limit_price).toBe('650');
    controller.setContext({account: SINOPAC, reference: REF});
    expect(controller.build().request).toBeNull();
    await controller.preview();
    expect(seen).toHaveLength(0);
  });

  it('explains why broker accounts cannot trade or go live', () => {
    const cathay = brokerAccount(
      'cathay',
      {status: 'TRADING_UNAVAILABLE', modes: []},
      {status: 'TRADING_UNAVAILABLE', notes: ['Awaiting the Cathay API SDK.']}
    );
    expect(
      modeAvailability(cathay).find(m => m.mode === 'PAPER')?.reason
    ).toMatch(/awaiting its broker API SDK/);
    const down = brokerAccount('sinopac', {status: 'DISCONNECTED', modes: []});
    expect(modeAvailability(down).find(m => m.mode === 'LIVE')?.reason).toMatch(
      /disconnected/
    );
    expect(
      modeAvailability(account()).find(m => m.mode === 'PAPER')?.available
    ).toBe(true);
  });
});
