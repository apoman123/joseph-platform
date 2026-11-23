/**
 * @fileoverview Unit tests for cash-equity order intents: board and odd
 * lots, tick-ladder rounding, daily price limits, broker time in force,
 * outside-RTH, account routing, rates, confirm labels and the ticket
 * controller's equity context.
 */
import {Decimal, type OrderIntentRequest} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  buildEquityIntentRequest,
  buildIntentRequest,
  defaultTicketInputs,
  describeAction,
  type TicketInputs,
} from './intent-builder';
import {TicketController, modeAvailability} from './ticket';
import {TWSE_STOCK, US_STOCK, account, brokerAccount} from './testing/fixtures';

const SINOPAC = brokerAccount('sinopac');
const IBKR = brokerAccount('ibkr', {}, {outside_rth: true});
const REF = Decimal.parse('585');

/**
 * Ticket inputs for an instrument.
 * @param instrumentId Instrument.
 * @param accountId Account.
 * @param patch Overrides.
 * @return Inputs.
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
 * Messages of a build result.
 * @param result Build result.
 * @return Messages.
 */
function messages(result: ReturnType<typeof buildIntentRequest>): string[] {
  return result.issues.map(issue => issue.message);
}

describe('TWSE orders', () => {
  it('sends board lots as shares with DAY and no leverage', () => {
    const result = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '2',
        limitPrice: '585',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC, reference: REF}
    );
    expect(result.request).toMatchObject({
      quantity_base: '2000',
      limit_price: '585',
      time_in_force: 'DAY',
      leverage: null,
      reduce_only: false,
      bracket: null,
    });
    expect(result.request).not.toHaveProperty('lot_type');
  });

  it('accepts odd lots below a board lot and rejects larger ones', () => {
    const odd = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        lotMode: 'odd',
        quantity: '350',
        orderType: 'market',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC, reference: REF}
    );
    expect(odd.request?.quantity_base).toBe('350');
    const tooMany = buildIntentRequest(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        lotMode: 'odd',
        quantity: '1200',
        orderType: 'market',
      }),
      TWSE_STOCK,
      REF,
      {account: SINOPAC}
    );
    expect(tooMany.request).toBeNull();
    expect(messages(tooMany).join(' ')).toMatch(/odd-lot order is 1 to 999/);
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

  it('refuses a time in force the broker does not take', () => {
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
      'This account accepts DAY for stocks and ETFs.'
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

describe('US orders through IBKR', () => {
  it('sends outside RTH only when the engine accepts it', () => {
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
    const plain = brokerAccount('ibkr');
    const refused = buildIntentRequest(base, US_STOCK, null, {account: plain});
    expect(refused.request).toBeNull();
    expect(messages(refused).join(' ')).toMatch(/does not accept outside-RTH/);
    const market = buildIntentRequest(
      {...base, orderType: 'market'},
      US_STOCK,
      Decimal.parse('182.4'),
      {account: IBKR}
    );
    expect(messages(market)).toContain(
      'Interactive Brokers fills outside regular hours only for limit orders.'
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
      'SinoPac Securities (Shioaji) accounts trade TWSE listings.'
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
    const controller = new TicketController(
      inputs(TWSE_STOCK.instrument_id, SINOPAC.account_id, {
        quantity: '1',
        limitPrice: '650',
      }),
      {
        client: {
          previewOrder: async (request: OrderIntentRequest) => {
            seen.push(request);
            throw new Error('not reached');
          },
        } as never,
      }
    );
    controller.setMarket(TWSE_STOCK, REF);
    expect(controller.build().request?.limit_price).toBe('650');
    controller.setContext({account: SINOPAC, reference: REF});
    expect(controller.build().request).toBeNull();
    await controller.preview();
    expect(seen).toHaveLength(0);
  });

  it('explains why broker accounts cannot go live', () => {
    const cathay = brokerAccount('cathay', {
      status: 'TRADING_UNAVAILABLE',
      modes: [],
    });
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
