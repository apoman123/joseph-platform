/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the DEMO accounts against the execution contract:
 * USD and TWD paper accounts, IBKR paper, SinoPac simulation and live
 * (credentials missing), Cathay awaiting its API SDK; routing by
 * `tradable_venues`; order types, DAY for sessions only, intraday odd lots,
 * outside RTH, closed-market rules, long-only cash and settled cash.
 */

import type {Instrument, OrderIntentRequest} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DemoExecutionClient} from './demo-execution-client';
import {
  DEMO_ACCOUNT_ID,
  DEMO_CATHAY_ACCOUNT_ID,
  DEMO_IBKR_ACCOUNT_ID,
  DEMO_SINOPAC_ACCOUNT_ID,
  DEMO_SINOPAC_LIVE_ACCOUNT_ID,
  DEMO_TWD_ACCOUNT_ID,
  demoAccount,
  demoRoutingProblem,
} from './instruments';

/** Friday 2027-01-15 08:00 UTC: NYSE and TWSE are both closed. */
const CLOSED = 1_800_000_000_000;
/** Monday 2027-01-18 02:00 UTC = 10:00 Taipei: TWSE is open. */
const TWSE_OPEN = Date.UTC(2027, 0, 18, 2);

/**
 * Builds a 100-share ACME DAY limit buy on the IBKR paper account.
 * @param patch Fields to override.
 * @return Request.
 */
function order(patch: Partial<OrderIntentRequest> = {}): OrderIntentRequest {
  return {
    account_id: DEMO_IBKR_ACCOUNT_ID,
    instrument_id: 'mock:stock:ACME',
    side: 'buy',
    position_effect: 'open',
    order_type: 'limit',
    quantity_base: '100',
    limit_price: '182.40',
    time_in_force: 'DAY',
    reduce_only: false,
    mode: 'PAPER',
    source: 'MANUAL_TICKET',
    ...patch,
  };
}

/**
 * Builds a one-board-lot TWSE limit buy on the SinoPac simulation account.
 * @param patch Fields to override.
 * @return Request.
 */
function twse(patch: Partial<OrderIntentRequest> = {}): OrderIntentRequest {
  return order({
    account_id: DEMO_SINOPAC_ACCOUNT_ID,
    instrument_id: 'mock:stock:9901',
    quantity_base: '1000',
    limit_price: '585',
    ...patch,
  });
}

/**
 * Builds a broker that runs scheduled tasks immediately.
 * @param now Fixed time in epoch milliseconds (UTC).
 * @return Broker.
 */
function broker(now = CLOSED): DemoExecutionClient {
  return new DemoExecutionClient({
    schedule: task => void task(),
    now: () => now,
    partialFills: false,
  });
}

/**
 * Previews a request and returns the ids of its failing checks.
 * @param client Broker.
 * @param request Request.
 * @return Failing check ids; empty when no check fails.
 */
async function failing(
  client: DemoExecutionClient,
  request: OrderIntentRequest
): Promise<string[]> {
  const preview = await client.previewOrder(request);
  return preview.risk.checks
    .filter(check => check.outcome === 'FAIL')
    .map(check => check.check);
}

describe('DEMO accounts', () => {
  it('lists paper accounts first, then the brokers, with capabilities', async () => {
    const {accounts} = await broker().listAccounts();
    expect(accounts.map(a => a.account_id)).toEqual([
      DEMO_ACCOUNT_ID,
      DEMO_TWD_ACCOUNT_ID,
      DEMO_IBKR_ACCOUNT_ID,
      DEMO_SINOPAC_ACCOUNT_ID,
      DEMO_SINOPAC_LIVE_ACCOUNT_ID,
      DEMO_CATHAY_ACCOUNT_ID,
    ]);
    expect(accounts.every(a => !a.modes.includes('LIVE'))).toBe(true);
    const [usd, twd, ibkr, sinopac, live, cathay] = accounts;
    expect(usd?.capabilities).toMatchObject({
      tradable_venues: ['nasdaq', 'nyse', 'cboe'],
      cash_account: true,
      simulation: true,
      currencies: ['USD'],
    });
    expect(twd?.capabilities.tradable_venues).toEqual(['twse', 'tpex']);
    expect(sinopac?.capabilities.tradable_venues).toEqual(['twse', 'tpex']);
    expect(cathay?.capabilities.tradable_venues).toEqual(['twse', 'tpex']);
    expect(twd?.settled_cash).toBe('3000000.00');
    expect(ibkr?.capabilities).toMatchObject({
      outside_rth: true,
      odd_lot: false,
      queue_market_when_closed: true,
      time_in_force: ['DAY', 'GTC', 'IOC'],
    });
    expect(sinopac?.capabilities).toMatchObject({
      odd_lot: true,
      queue_market_when_closed: false,
      order_types: ['market', 'limit'],
    });
    expect(live).toMatchObject({status: 'DISCONNECTED', equity: null});
    expect(live?.capabilities.notes.join(' ')).toMatch(/execution server/);
    expect(cathay?.status).toBe('TRADING_UNAVAILABLE');
    expect(cathay?.capabilities.notes.join(' ')).toMatch(/Cathay API SDK/);
    expect(
      (await broker().getAccountSummary(DEMO_SINOPAC_ACCOUNT_ID)).currency
    ).toBe('TWD');
    await expect(broker().getAccountSummary('nope')).rejects.toThrow(
      /Unknown account/
    );
  });

  it('routes by tradable venues like the engine', async () => {
    const client = broker();
    expect(await failing(client, order())).toEqual([]);
    expect(await failing(client, order({account_id: DEMO_ACCOUNT_ID}))).toEqual(
      []
    );
    expect(
      await failing(client, order({account_id: DEMO_SINOPAC_ACCOUNT_ID}))
    ).toContain('account.routing');
    expect(
      await failing(client, twse({account_id: DEMO_ACCOUNT_ID}))
    ).toContain('account.routing');
    expect(
      await failing(client, twse({account_id: DEMO_TWD_ACCOUNT_ID}))
    ).toEqual([]);
    const crypto = order({
      instrument_id: 'mock:perp:BTCUSD',
      quantity_base: '0.010',
      limit_price: '95000',
      time_in_force: 'GTC',
    });
    expect(
      await failing(client, {...crypto, account_id: DEMO_TWD_ACCOUNT_ID})
    ).toContain('account.routing');
    expect(await failing(client, crypto)).toContain('account.routing');
    expect(
      await failing(client, {...crypto, account_id: DEMO_ACCOUNT_ID})
    ).toEqual([]);
    const spec = demoAccount(DEMO_TWD_ACCOUNT_ID);
    // Deliberate partial mock: demoRoutingProblem reads only `key`,
    // `calendar` and `instrument_id` of a stock with no listing exchange.
    const unlisted = {
      instrument_id: 'x',
      key: {venue: 'mock', market_type: 'stock'},
    } as unknown as Instrument;
    expect(spec && demoRoutingProblem(spec, unlisted)).toMatch(
      /does not route/
    );
  });

  it('refuses disconnected and SDK-less broker accounts', async () => {
    const client = broker();
    expect(
      await failing(client, twse({account_id: DEMO_CATHAY_ACCOUNT_ID}))
    ).toContain('account.connected');
    expect(
      await failing(client, twse({account_id: DEMO_SINOPAC_LIVE_ACCOUNT_ID}))
    ).toContain('account.connected');
  });

  it('applies order types, DAY, odd lots, outside RTH and long-only rules', async () => {
    const client = broker(TWSE_OPEN);
    expect(
      await failing(
        client,
        twse({
          order_type: 'stop_market',
          trigger_price: '600',
          limit_price: null,
        })
      )
    ).toContain('size.order_type');
    expect(await failing(client, twse({quantity_base: '350'}))).toEqual([]);
    expect(
      await failing(
        client,
        twse({quantity_base: '350', order_type: 'market', limit_price: null})
      )
    ).toContain('size.board_lot');
    expect(
      await failing(client, twse({quantity_base: '350', time_in_force: 'IOC'}))
    ).toContain('size.board_lot');
    expect(await failing(client, twse({quantity_base: '1500'}))).toContain(
      'size.board_lot'
    );
    expect(await failing(client, twse({outside_rth: true}))).toContain(
      'market.outside_rth'
    );
    expect(
      await failing(client, twse({side: 'sell', quantity_base: '1000'}))
    ).toContain('exposure.short_selling');
    expect(
      await failing(
        client,
        order({
          account_id: DEMO_ACCOUNT_ID,
          instrument_id: 'mock:perp:BTCUSD',
          quantity_base: '0.010',
          limit_price: '95000',
        })
      )
    ).toContain('market.time_in_force');
    const offLadder = await client.previewOrder(twse({limit_price: '585.5'}));
    // The DEMO broker normalizes to the ladder like the engine.
    expect(offLadder.normalized.limit_price).toBe('586');
  });

  it('routes the TPEx bond ETF to Taiwan accounts in board lots', async () => {
    const client = broker(TWSE_OPEN);
    const etf = (patch: Partial<OrderIntentRequest> = {}) =>
      twse({
        instrument_id: 'mock:etf:00999B',
        quantity_base: '2000',
        limit_price: '26.20',
        ...patch,
      });
    expect(await failing(client, etf())).toEqual([]);
    expect(
      await failing(client, etf({account_id: DEMO_TWD_ACCOUNT_ID}))
    ).toEqual([]);
    expect(
      await failing(client, etf({account_id: DEMO_IBKR_ACCOUNT_ID}))
    ).toContain('account.routing');
    const mixed = await client.previewOrder(etf({quantity_base: '1500'}));
    expect(
      mixed.risk.checks.find(c => c.check === 'size.board_lot')?.message
    ).toMatch(/^TPEx orders are whole board lots of 1000 shares/);
    // ETF ladder: 0.01 below 50 TWD.
    const offTick = await client.previewOrder(etf({limit_price: '26.204'}));
    expect(offTick.normalized.limit_price).toBe('26.2');
  });

  it('applies closed-market rules', async () => {
    const client = broker(CLOSED);
    const market = {order_type: 'market' as const, limit_price: null};
    expect(await failing(client, twse(market))).toContain('market.session');
    expect(await failing(client, order(market))).toEqual([]);
    const queued = await client.previewOrder(order(market));
    expect(
      queued.risk.checks.find(c => c.check === 'market.session')?.outcome
    ).toBe('WARN');
    expect(
      await failing(client, order({...market, time_in_force: 'GTC'}))
    ).toContain('market.session');
    expect(await failing(client, order({time_in_force: 'IOC'}))).toContain(
      'market.session'
    );
    const resting = await client.previewOrder(order({outside_rth: true}));
    expect(
      resting.risk.checks.find(c => c.check === 'market.session')?.message
    ).toMatch(/extended hours/);
  });

  it('books cash positions and fills per account and currency', async () => {
    const client = broker();
    const preview = await client.previewOrder(
      order({order_type: 'market', limit_price: null, quantity_base: '10'})
    );
    expect(preview.allowed).toBe(true);
    expect(preview.margin_required).toBe(preview.notional);
    await client.submitIntent(
      {...preview.normalized, preview_token: preview.preview_token},
      'key-1'
    );
    const positions = await client.listPositions(DEMO_IBKR_ACCOUNT_ID);
    expect(positions.positions).toHaveLength(1);
    expect(positions.positions[0]).toMatchObject({
      account_id: DEMO_IBKR_ACCOUNT_ID,
      liquidation_price: null,
    });
    expect((await client.listPositions(DEMO_ACCOUNT_ID)).positions).toEqual([]);
    const fills = await client.listFills(DEMO_IBKR_ACCOUNT_ID);
    expect(fills.fills[0]?.fee_asset).toBe('USD');
    const summary = await client.getAccountSummary(DEMO_IBKR_ACCOUNT_ID);
    expect(summary.positions).toHaveLength(1);
    expect(Number(summary.settled_cash)).toBeLessThan(100_000);
    expect(summary.unsettled_cash).toBe('0.00');
  });
});
