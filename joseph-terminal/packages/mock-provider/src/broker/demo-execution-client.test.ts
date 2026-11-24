/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the DEMO paper broker and user stream.
 */

import {
  ApiError,
  type OrderIntentRequest,
  type StreamEnvelope,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DemoExecutionClient} from './demo-execution-client';
import {DemoUserStream} from './demo-user-stream';
import {DEMO_ACCOUNT_ID} from './instruments';

/** Manual scheduler for deterministic lifecycles. */
class ManualScheduler {
  private readonly tasks: Array<() => void> = [];

  /**
   * Queues a task.
   * @param task Task.
   */
  schedule = (task: () => void): void => {
    this.tasks.push(task);
  };

  /** Runs only the tasks queued so far (one simulated latency step). */
  step(): void {
    const batch = this.tasks.splice(0);
    for (const task of batch) {
      task();
    }
  }

  /** Runs tasks until the queue is empty. */
  flush(): void {
    for (let i = 0; i < 100 && this.tasks.length; i += 1) {
      const batch = this.tasks.splice(0);
      for (const task of batch) {
        task();
      }
    }
  }
}

/**
 * Builds a request for BTC.
 * @param patch Overrides.
 * @return Request.
 */
function request(patch: Partial<OrderIntentRequest> = {}): OrderIntentRequest {
  return {
    account_id: DEMO_ACCOUNT_ID,
    instrument_id: 'mock:perp:BTCUSD',
    side: 'buy',
    position_effect: 'open',
    order_type: 'limit',
    quantity_base: '0.010',
    limit_price: '95100',
    time_in_force: 'GTC',
    reduce_only: false,
    mode: 'PAPER',
    source: 'MANUAL_TICKET',
    ...patch,
  };
}

/**
 * Creates a broker with a manual scheduler.
 * @param partialFills Whether to split fills.
 * @return Broker and scheduler.
 */
function setup(partialFills = false): {
  broker: DemoExecutionClient;
  clock: ManualScheduler;
} {
  const clock = new ManualScheduler();
  const broker = new DemoExecutionClient({
    schedule: clock.schedule,
    now: () => 1_800_000_000_000,
    partialFills,
  });
  return {broker, clock};
}

describe('DemoExecutionClient', () => {
  it('previews with rounding, risk checks and a token', async () => {
    const {broker} = setup();
    const preview = await broker.previewOrder(
      request({quantity_base: '0.0109', limit_price: '95100.04'})
    );
    expect(preview.allowed).toBe(true);
    expect(preview.normalized.quantity_base).toBe('0.01');
    expect(preview.normalized.limit_price).toBe('95100');
    expect(preview.risk.checks.map(c => c.check)).toContain(
      'kill_switch.clear'
    );
    expect(preview.preview_token).toMatch(/^demo-preview/);
  });

  it('rejects live mode and tiny orders', async () => {
    const {broker} = setup();
    const live = await broker.previewOrder(request({mode: 'LIVE'}));
    expect(live.allowed).toBe(false);
    expect(live.risk.checks.find(c => c.check === 'mode.permitted')?.code).toBe(
      'LIVE_TRADING_DISABLED'
    );
    const tiny = await broker.previewOrder(request({quantity_base: '0.00001'}));
    expect(tiny.risk.decision).toBe('REJECT');
  });

  it('replays an idempotency key and rejects a conflicting body', async () => {
    const {broker} = setup();
    const first = await broker.submitIntent(request(), 'key-1');
    const again = await broker.submitIntent(request(), 'key-1');
    expect(again.replayed).toBe(true);
    expect(again.orders[0]?.order_id).toBe(first.orders[0]?.order_id);
    expect((await broker.listOrders()).orders).toHaveLength(1);
    await expect(
      broker.submitIntent(request({quantity_base: '0.02'}), 'key-1')
    ).rejects.toMatchObject({body: {code: 'IDEMPOTENCY_CONFLICT'}});
  });

  it('enforces preview tokens', async () => {
    const {broker} = setup();
    const preview = await broker.previewOrder(request());
    await expect(
      broker.submitIntent(
        request({quantity_base: '0.5', preview_token: preview.preview_token}),
        'k'
      )
    ).rejects.toMatchObject({body: {code: 'PREVIEW_REQUIRED'}});
    await expect(
      broker.submitIntent(request({preview_token: 'nope'}), 'k2')
    ).rejects.toBeInstanceOf(ApiError);
    const ok = await broker.submitIntent(
      {...preview.normalized, preview_token: preview.preview_token},
      'k3'
    );
    expect(ok.orders).toHaveLength(1);
  });

  it('walks SUBMITTING → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED and opens a position', async () => {
    const {broker, clock} = setup(true);
    const stream = new DemoUserStream(broker);
    const seen: StreamEnvelope[] = [];
    stream.subscribe(
      [{channel: 'orders'}, {channel: 'positions'}, {channel: 'fills'}],
      env => seen.push(env)
    );
    const {orders} = await broker.submitIntent(
      request({order_type: 'market', limit_price: null}),
      'k'
    );
    expect(orders[0]?.state).toBe('SUBMITTING');
    clock.flush();
    const states = seen
      .filter(e => e.type === 'order.updated')
      .map(e => (e.payload as {state: string}).state);
    expect(states).toEqual([
      'SUBMITTING',
      'ACKNOWLEDGED',
      'PARTIALLY_FILLED',
      'FILLED',
    ]);
    const sequences = seen.map(e => e.sequence);
    expect(
      sequences.every((s, i) => i === 0 || s > (sequences[i - 1] ?? 0))
    ).toBe(true);
    const {positions} = await broker.listPositions();
    expect(positions[0]).toMatchObject({side: 'long', quantity_base: '0.01'});
    expect((await broker.listFills()).fills).toHaveLength(2);
  });

  it('keeps non-marketable limits resting until canceled', async () => {
    const {broker, clock} = setup();
    const {orders} = await broker.submitIntent(
      request({limit_price: '90000'}),
      'k'
    );
    clock.flush();
    const id = orders[0]?.order_id ?? '';
    expect((await broker.listOrders()).orders[0]?.state).toBe('ACKNOWLEDGED');
    expect((await broker.cancelOrder(id)).state).toBe('CANCEL_PENDING');
    clock.flush();
    expect((await broker.listOrders()).orders[0]?.state).toBe('CANCELED');
    await expect(broker.cancelOrder(id)).rejects.toMatchObject({
      body: {code: 'CONFLICT'},
    });
  });

  it('amends with optimistic concurrency', async () => {
    const {broker, clock} = setup();
    const {orders} = await broker.submitIntent(
      request({limit_price: '90000'}),
      'k'
    );
    clock.flush();
    const current = (await broker.listOrders()).orders[0];
    if (!current) throw new Error('missing order');
    await expect(
      broker.amendOrder(current.order_id, {
        limit_price: '91000',
        expected_version: current.version + 5,
      })
    ).rejects.toMatchObject({body: {code: 'CONFLICT'}});
    const amended = await broker.amendOrder(current.order_id, {
      limit_price: '91000.06',
      expected_version: current.version,
    });
    expect(amended.limit_price).toBe('91000.1');
    expect(orders).toHaveLength(1);
  });

  it('blocks opening orders under a kill switch but allows reduce-only closes', async () => {
    const {broker, clock} = setup();
    await broker.submitIntent(
      request({order_type: 'market', limit_price: null}),
      'open'
    );
    clock.flush();
    await broker.setKillSwitch({
      scope: 'GLOBAL',
      engage: true,
      cancel_open_orders: false,
      flatten_positions: false,
      reason: 'test',
    });
    const blocked = await broker.previewOrder(request());
    expect(blocked.allowed).toBe(false);
    expect(
      blocked.risk.checks.find(c => c.check === 'kill_switch.clear')?.code
    ).toBe('KILL_SWITCH_ENGAGED');
    const submitted = await broker.submitIntent(request(), 'blocked');
    expect(submitted.orders).toHaveLength(0);
    expect(submitted.risk.decision).toBe('BLOCK');
    const [position] = (await broker.listPositions()).positions;
    if (!position) throw new Error('missing position');
    const closePreview = await broker.closePositionPreview(
      position.position_id,
      {fraction: '1', order_type: 'market'}
    );
    expect(closePreview.allowed).toBe(true);
    expect(closePreview.normalized.reduce_only).toBe(true);
    await broker.closePosition(
      position.position_id,
      {
        fraction: '1',
        order_type: 'market',
        preview_token: closePreview.preview_token,
      },
      'close'
    );
    clock.flush();
    expect((await broker.listPositions()).positions).toHaveLength(0);
  });

  it('fully closes a position even when fills arrive in parts', async () => {
    const {broker, clock} = setup(true);
    await broker.submitIntent(
      request({order_type: 'market', limit_price: null}),
      'open-partial'
    );
    clock.flush();
    const [position] = (await broker.listPositions()).positions;
    if (!position) throw new Error('missing position');
    expect(position.quantity_base).toBe('0.01');
    const preview = await broker.closePositionPreview(position.position_id, {
      fraction: '1',
      order_type: 'market',
    });
    await broker.closePosition(
      position.position_id,
      {
        fraction: '1',
        order_type: 'market',
        preview_token: preview.preview_token,
      },
      'close-partial'
    );
    clock.flush();
    expect((await broker.listPositions()).positions).toHaveLength(0);
  });

  it('closes what was open when the close raced a partial fill', async () => {
    const {broker, clock} = setup(true);
    await broker.submitIntent(
      request({order_type: 'market', limit_price: null}),
      'open-race'
    );
    clock.step();
    clock.step();
    const [partial] = (await broker.listPositions()).positions;
    if (!partial) throw new Error('missing partial position');
    expect(partial.quantity_base).toBe('0.005');
    const preview = await broker.closePositionPreview(partial.position_id, {
      fraction: '1',
      order_type: 'market',
    });
    await broker.closePosition(
      partial.position_id,
      {
        fraction: '1',
        order_type: 'market',
        preview_token: preview.preview_token,
      },
      'close-race'
    );
    clock.flush();
    const remaining = (await broker.listPositions()).positions;
    const total = remaining.reduce(
      (sum, p) => sum + Number(p.quantity_base),
      0
    );
    expect(total).toBeCloseTo(0.005, 9);
  });

  it('places simulated brackets and cancels them when flat', async () => {
    const {broker, clock} = setup();
    await broker.submitIntent(
      request({
        order_type: 'market',
        limit_price: null,
        bracket: {
          stop_loss: {trigger: '94000', basis: 'mark'},
          take_profit: [{trigger: '97000', fraction: '1'}],
        },
      }),
      'b'
    );
    clock.flush();
    const children = (await broker.listOrders()).orders.filter(
      o => o.role !== 'PRIMARY'
    );
    expect(children.map(o => o.role).sort()).toEqual([
      'STOP_LOSS',
      'TAKE_PROFIT',
    ]);
    expect(
      children.every(o => o.reduce_only && o.state === 'ACKNOWLEDGED')
    ).toBe(true);
    await broker.setKillSwitch({
      scope: 'GLOBAL',
      engage: true,
      cancel_open_orders: false,
      flatten_positions: true,
      reason: 'flatten',
    });
    clock.flush();
    expect((await broker.listPositions()).positions).toHaveLength(0);
    expect(
      (await broker.listOrders()).orders
        .filter(o => o.role !== 'PRIMARY')
        .every(o => o.state === 'CANCELED')
    ).toBe(true);
  });

  it('keeps a verifiable audit chain', async () => {
    const {broker} = setup();
    await broker.submitIntent(request(), 'a');
    await broker.setKillSwitch({
      scope: 'ACCOUNT',
      target_id: DEMO_ACCOUNT_ID,
      engage: true,
      cancel_open_orders: true,
      flatten_positions: false,
      reason: 'x',
    });
    const audit = await broker.listAuditEvents();
    expect(audit.chain_valid).toBe(true);
    expect(audit.events.map(e => e.action)).toEqual([
      'kill_switch.engage',
      'order.submit',
    ]);
  });

  it('keeps AUTO_LIVE deployments pending consent', async () => {
    const {broker} = setup();
    const deployment = await broker.createDeployment({
      strategy_version_id: 'x:v1',
      account_id: DEMO_ACCOUNT_ID,
      mode: 'AUTO_LIVE',
      limits: {
        max_notional: '1000',
        max_open_positions: 1,
        max_daily_loss: '100',
        allowed_instruments: [],
        max_leverage: '2',
      },
      sizing: {model: 'FIXED_QTY', fixed_qty: '0.001'},
    });
    expect(deployment.state).toBe('PENDING_CONSENT');
    expect(
      (await broker.disableDeployment(deployment.deployment_id)).state
    ).toBe('DISABLED');
  });

  it('reports a DEMO account that never offers LIVE', async () => {
    const {broker} = setup();
    const {accounts} = await broker.listAccounts();
    expect(accounts[0]?.modes).toEqual(['PAPER', 'SHADOW']);
    expect(accounts[0]?.capabilities.notes.join(' ')).toMatch(/DEMO/);
  });
});
