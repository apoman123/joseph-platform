/**
 * @fileoverview Tests for the DEMO broker accounts: IBKR paper, SinoPac
 * simulation and live (credentials missing), Cathay awaiting its API SDK,
 * routing of stocks to brokers, TWSE lots and ladder ticks, and per-account
 * cash and positions.
 */
import type {OrderIntentRequest, TradingCapabilities} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DemoExecutionClient} from './demo-execution-client';
import {
  DEMO_ACCOUNT_ID,
  DEMO_CATHAY_ACCOUNT_ID,
  DEMO_IBKR_ACCOUNT_ID,
  DEMO_SINOPAC_ACCOUNT_ID,
  DEMO_SINOPAC_LIVE_ACCOUNT_ID,
  demoAccount,
  demoRoutingProblem,
} from './instruments';

/**
 * Builds a stock order.
 * @param patch Overrides.
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
    time_in_force: 'GTC',
    reduce_only: false,
    mode: 'PAPER',
    source: 'MANUAL_TICKET',
    ...patch,
  };
}

/**
 * Broker running tasks immediately.
 * @return Broker.
 */
function broker(): DemoExecutionClient {
  return new DemoExecutionClient({
    schedule: task => task(),
    now: () => 1_800_000_000_000,
    partialFills: false,
  });
}

/**
 * Failing check ids of a preview.
 * @param client Broker.
 * @param request Request.
 * @return Check ids.
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

describe('DEMO broker accounts', () => {
  it('lists the crypto paper account first, then the brokers', async () => {
    const {accounts} = await broker().listAccounts();
    expect(accounts.map(a => a.account_id)).toEqual([
      DEMO_ACCOUNT_ID,
      DEMO_IBKR_ACCOUNT_ID,
      DEMO_SINOPAC_ACCOUNT_ID,
      DEMO_SINOPAC_LIVE_ACCOUNT_ID,
      DEMO_CATHAY_ACCOUNT_ID,
    ]);
    expect(accounts.every(a => !a.modes.includes('LIVE'))).toBe(true);
    const cathay = accounts.find(a => a.venue === 'cathay');
    expect(cathay?.status).toBe('TRADING_UNAVAILABLE');
    expect(cathay?.capabilities.notes.join(' ')).toMatch(/API SDK/);
    const live = accounts.find(
      a => a.account_id === DEMO_SINOPAC_LIVE_ACCOUNT_ID
    );
    expect(live).toMatchObject({status: 'DISCONNECTED', equity: null});
    const ibkr = accounts.find(a => a.venue === 'ibkr');
    const caps = ibkr?.capabilities as unknown as Record<string, unknown>;
    expect(caps.outside_rth).toBe(true);
    expect(ibkr?.capabilities.time_in_force).toEqual(['DAY', 'GTC']);
    expect(
      (await broker().getAccountSummary(DEMO_SINOPAC_ACCOUNT_ID)).currency
    ).toBe('TWD');
    await expect(broker().getAccountSummary('nope')).rejects.toThrow(
      /Unknown account/
    );
  });

  it('routes stocks only to the broker that trades them', async () => {
    const client = broker();
    expect(await failing(client, order())).toEqual([]);
    expect(
      await failing(client, order({account_id: DEMO_SINOPAC_ACCOUNT_ID}))
    ).toContain('account.routing');
    expect(
      await failing(client, order({account_id: DEMO_ACCOUNT_ID}))
    ).toContain('account.routing');
    const twse = order({
      account_id: DEMO_SINOPAC_ACCOUNT_ID,
      instrument_id: 'mock:stock:9901',
      quantity_base: '2000',
      limit_price: '585',
    });
    expect(await failing(client, twse)).toEqual([]);
    const spec = demoAccount(DEMO_IBKR_ACCOUNT_ID);
    expect(
      spec && demoRoutingProblem(spec, {key: {market_type: 'perp'}} as never)
    ).toMatch(/Nasdaq and NYSE/);
  });

  it('refuses disconnected and SDK-less broker accounts', async () => {
    const client = broker();
    const twse = order({
      instrument_id: 'mock:stock:9901',
      quantity_base: '1000',
      limit_price: '585',
    });
    expect(
      await failing(client, {...twse, account_id: DEMO_CATHAY_ACCOUNT_ID})
    ).toContain('account.connected');
    expect(
      await failing(client, {
        ...twse,
        account_id: DEMO_SINOPAC_LIVE_ACCOUNT_ID,
      })
    ).toContain('account.connected');
  });

  it('checks TWSE board lots and the tick ladder', async () => {
    const client = broker();
    const base = order({
      account_id: DEMO_SINOPAC_ACCOUNT_ID,
      instrument_id: 'mock:stock:9901',
      limit_price: '585',
    });
    expect(await failing(client, {...base, quantity_base: '1500'})).toContain(
      'size.board_lot'
    );
    expect(await failing(client, {...base, quantity_base: '999'})).toEqual([]);
    const offLadder = await client.previewOrder({
      ...base,
      quantity_base: '1000',
      limit_price: '585.5',
    });
    // The DEMO broker normalizes to the ladder like the engine.
    expect(offLadder.normalized.limit_price).toBe('586');
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
    expect(Number(summary.available_margin)).toBeLessThan(100_000);
  });

  it('exposes capabilities without unknown fields for the crypto account', () => {
    const caps: TradingCapabilities | undefined =
      demoAccount(DEMO_ACCOUNT_ID)?.capabilities;
    expect(caps && 'outside_rth' in caps).toBe(false);
  });
});
