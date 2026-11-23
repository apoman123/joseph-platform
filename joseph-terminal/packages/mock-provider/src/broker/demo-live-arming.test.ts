/**
 * @fileoverview Tests for server-side LIVE arming in the DEMO broker:
 * refusals (paper, unverified, unknown accounts, wrong step-up code, bad
 * lifetime), arming until a time, expiry, revocation, `LIVE_NOT_ARMED` in
 * the risk decision and on amend, risk-reducing closes while disarmed and
 * the audit trail.
 */
import {ApiError, type OrderIntentRequest} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  DemoExecutionClient,
  type DemoBrokerEvent,
} from './demo-execution-client';
import {
  DEMO_ACCOUNT_ID,
  DEMO_IBKR_ACCOUNT_ID,
  DEMO_LIVE_ARM_MAX_MS,
  DEMO_LIVE_REHEARSAL_ACCOUNT_ID,
  DEMO_STEP_UP_CODE,
} from './instruments';

/** Wednesday 2027-01-20 15:00 UTC = 10:00 New York: NYSE is open. */
const NY_OPEN = Date.UTC(2027, 0, 20, 15);
/** One hour. */
const HOUR = 3_600_000;

/** Controllable clock and scheduler. */
class Sim {
  nowMs = NY_OPEN;
  private readonly tasks: (() => void)[] = [];

  /**
   * Queues a task.
   * @param task Task.
   */
  schedule = (task: () => void): void => {
    this.tasks.push(task);
  };

  /**
   * Returns the simulated time.
   * @return UTC ms.
   */
  now = (): number => this.nowMs;

  /** Runs queued tasks until none are left. */
  flush(): void {
    for (let i = 0; i < 100 && this.tasks.length; i += 1) {
      for (const task of this.tasks.splice(0)) task();
    }
  }
}

/**
 * Builds a rehearsal broker.
 * @param liveRehearsal Whether the LIVE rehearsal account exists.
 * @return Broker and simulator.
 */
function setup(liveRehearsal = true): {broker: DemoExecutionClient; sim: Sim} {
  const sim = new Sim();
  const broker = new DemoExecutionClient({
    now: sim.now,
    schedule: sim.schedule,
    partialFills: false,
    liveRehearsal,
  });
  return {broker, sim};
}

/**
 * A LIVE ACME order on the rehearsal account.
 * @param patch Overrides.
 * @return Request.
 */
function live(patch: Partial<OrderIntentRequest> = {}): OrderIntentRequest {
  return {
    account_id: DEMO_LIVE_REHEARSAL_ACCOUNT_ID,
    instrument_id: 'mock:stock:ACME',
    side: 'buy',
    position_effect: 'open',
    order_type: 'limit',
    quantity_base: '10',
    limit_price: '182.40',
    time_in_force: 'DAY',
    reduce_only: false,
    mode: 'LIVE',
    source: 'MANUAL_TICKET',
    ...patch,
  };
}

/**
 * Awaits a rejection and returns the contract error code and status.
 * @param promise Call expected to fail.
 * @return `[status, code]`.
 */
async function failure(promise: Promise<unknown>): Promise<[number, string]> {
  try {
    await promise;
  } catch (err) {
    if (err instanceof ApiError) return [err.status, err.body.code];
    throw err;
  }
  throw new Error('expected an ApiError');
}

/**
 * Arms the rehearsal account.
 * @param broker Broker.
 * @param durationMs Lifetime.
 * @return Summary.
 */
function arm(broker: DemoExecutionClient, durationMs = HOUR) {
  return broker.grantAccountLiveConsent(DEMO_LIVE_REHEARSAL_ACCOUNT_ID, {
    step_up_code: DEMO_STEP_UP_CODE,
    acknowledge_risk: true,
    duration_ms: durationMs,
  });
}

describe('DEMO live arming', () => {
  it('keeps the default DEMO without any LIVE account', async () => {
    const {broker} = setup(false);
    const {accounts} = await broker.listAccounts();
    expect(accounts.some(a => a.modes.includes('LIVE'))).toBe(false);
    expect(accounts.every(a => a.live_armed_until_ms === null)).toBe(true);
    expect(
      await failure(broker.getAccountSummary(DEMO_LIVE_REHEARSAL_ACCOUNT_ID))
    ).toEqual([404, 'NOT_FOUND']);
  });

  it('refuses paper, unverified and unknown accounts with clear errors', async () => {
    const {broker} = setup();
    const body = {
      step_up_code: DEMO_STEP_UP_CODE,
      acknowledge_risk: true as const,
    };
    expect(
      await failure(broker.grantAccountLiveConsent(DEMO_ACCOUNT_ID, body))
    ).toEqual([403, 'TRADE_MODE_DISABLED']);
    expect(
      await failure(broker.grantAccountLiveConsent(DEMO_IBKR_ACCOUNT_ID, body))
    ).toEqual([403, 'TRADE_MODE_DISABLED']);
    expect(
      await failure(broker.grantAccountLiveConsent('acct-nope', body))
    ).toEqual([404, 'NOT_FOUND']);
    expect(await failure(broker.revokeAccountLiveConsent('acct-nope'))).toEqual(
      [404, 'NOT_FOUND']
    );
    const {events} = await broker.listAuditEvents();
    const denied = events.filter(e => e.action === 'live_arming.denied');
    expect(denied.map(e => e.account_id).sort()).toEqual(
      [DEMO_ACCOUNT_ID, DEMO_IBKR_ACCOUNT_ID].sort()
    );
    expect(denied[0]?.payload).toMatchObject({code: 'TRADE_MODE_DISABLED'});
  });

  it('needs the step-up code, the acknowledgement and a bounded lifetime', async () => {
    const {broker} = setup();
    const id = DEMO_LIVE_REHEARSAL_ACCOUNT_ID;
    expect(
      await failure(
        broker.grantAccountLiveConsent(id, {
          step_up_code: '000000',
          acknowledge_risk: true,
        })
      )
    ).toEqual([403, 'STEP_UP_REQUIRED']);
    expect(
      await failure(
        broker.grantAccountLiveConsent(id, {
          step_up_code: DEMO_STEP_UP_CODE,
          acknowledge_risk: false as true,
        })
      )
    ).toEqual([422, 'VALIDATION_FAILED']);
    for (const duration of [0, DEMO_LIVE_ARM_MAX_MS + 1, 1.5]) {
      expect(await failure(arm(broker, duration))).toEqual([
        422,
        'VALIDATION_FAILED',
      ]);
    }
    expect((await broker.getAccountSummary(id)).live_armed_until_ms).toBeNull();
    const {events} = await broker.listAuditEvents(id);
    expect(events.map(e => e.action)).toEqual(
      Array(5).fill('live_arming.denied')
    );
  });

  it('arms until the requested time, expires and revokes', async () => {
    const {broker, sim} = setup();
    const seen: DemoBrokerEvent[] = [];
    broker.onEvent(event => seen.push(event));
    const id = DEMO_LIVE_REHEARSAL_ACCOUNT_ID;
    const armed = await arm(broker, 4 * HOUR);
    expect(armed.live_armed_until_ms).toBe(NY_OPEN + 4 * HOUR);
    expect(armed.modes).toEqual(['LIVE']);
    expect(seen.at(-1)).toMatchObject({
      type: 'account.updated',
      payload: {account_id: id, live_armed_until_ms: NY_OPEN + 4 * HOUR},
    });
    const listed = (await broker.listAccounts()).accounts.find(
      a => a.account_id === id
    );
    expect(listed?.live_armed_until_ms).toBe(NY_OPEN + 4 * HOUR);
    sim.nowMs = NY_OPEN + 4 * HOUR;
    expect((await broker.getAccountSummary(id)).live_armed_until_ms).toBeNull();
    const defaulted = await broker.grantAccountLiveConsent(id, {
      step_up_code: ` ${DEMO_STEP_UP_CODE} `,
      acknowledge_risk: true,
    });
    expect(defaulted.live_armed_until_ms).toBe(
      sim.nowMs + DEMO_LIVE_ARM_MAX_MS
    );
    const revoked = await broker.revokeAccountLiveConsent(id);
    expect(revoked.live_armed_until_ms).toBeNull();
    const again = await broker.revokeAccountLiveConsent(id);
    expect(again.live_armed_until_ms).toBeNull();
    const {events} = await broker.listAuditEvents(id);
    expect(events.map(e => e.action).reverse()).toEqual([
      'live_arming.granted',
      'live_arming.granted',
      'live_arming.revoked',
      'live_arming.revoked',
    ]);
    expect(events.at(-1)?.payload).toMatchObject({
      expires_at_ms: NY_OPEN + 4 * HOUR,
      granted_by: 'demo-user',
    });
    expect(events[1]?.payload).toMatchObject({
      was_armed_until_ms: sim.nowMs + DEMO_LIVE_ARM_MAX_MS,
    });
    expect((await broker.listAuditEvents()).chain_valid).toBe(true);
  });

  it('blocks unarmed manual LIVE orders with LIVE_NOT_ARMED', async () => {
    const {broker, sim} = setup();
    const blocked = await broker.previewOrder(live());
    expect(blocked.allowed).toBe(false);
    expect(blocked.risk.decision).toBe('BLOCK');
    const mode = blocked.risk.checks.find(c => c.check === 'mode.permitted');
    expect(mode).toMatchObject({outcome: 'FAIL', code: 'LIVE_NOT_ARMED'});
    const submitted = await broker.submitIntent(
      {...live(), preview_token: blocked.preview_token},
      'unarmed-1'
    );
    expect(submitted.risk.decision).toBe('BLOCK');
    expect(submitted.orders).toEqual([]);
    await arm(broker);
    const allowed = await broker.previewOrder(live());
    expect(allowed.allowed).toBe(true);
    expect(
      allowed.risk.checks.find(c => c.check === 'mode.permitted')?.message
    ).toMatch(/^Live is armed until 16:00 UTC/);
    const strategy = await broker.previewOrder(
      live({source: 'STRATEGY', strategy_version_id: 's:v1'})
    );
    expect(
      strategy.risk.checks.find(c => c.check === 'mode.permitted')?.code
    ).toBe('TRADE_MODE_DISABLED');
    const emergency = await broker.previewOrder(live({source: 'EMERGENCY'}));
    expect(
      emergency.risk.checks.find(c => c.check === 'mode.permitted')?.outcome
    ).toBe('PASS');
    const placed = await broker.submitIntent(
      {...live(), preview_token: allowed.preview_token},
      'armed-1'
    );
    expect(placed.orders).toHaveLength(1);
    expect(placed.orders[0]?.mode).toBe('LIVE');
    sim.flush();
    sim.nowMs += HOUR;
    const expired = await broker.previewOrder(live());
    expect(
      expired.risk.checks.find(c => c.check === 'mode.permitted')?.code
    ).toBe('LIVE_NOT_ARMED');
  });

  it('lets risk-reducing closes through while disarmed and refuses amends', async () => {
    const {broker, sim} = setup();
    await arm(broker);
    const buy = await broker.submitIntent(
      live({order_type: 'market', limit_price: null}),
      'buy-1'
    );
    expect(buy.risk.decision).toBe('ALLOW');
    sim.flush();
    const resting = await broker.submitIntent(
      live({limit_price: '150.00'}),
      'rest-1'
    );
    sim.flush();
    const working = resting.orders[0];
    if (!working) throw new Error('no resting order');
    const {positions} = await broker.listPositions(
      DEMO_LIVE_REHEARSAL_ACCOUNT_ID
    );
    const position = positions[0];
    if (!position) throw new Error('no position');
    expect(position).toMatchObject({mode: 'LIVE', quantity_base: '10'});
    await broker.revokeAccountLiveConsent(DEMO_LIVE_REHEARSAL_ACCOUNT_ID);
    const close = await broker.closePositionPreview(position.position_id, {
      fraction: '1',
      order_type: 'market',
    });
    expect(close.allowed).toBe(true);
    expect(close.normalized).toMatchObject({mode: 'LIVE', reduce_only: true});
    expect(
      close.risk.checks.find(c => c.check === 'mode.permitted')
    ).toMatchObject({outcome: 'WARN', code: null});
    const tooBig = await broker.previewOrder(
      live({side: 'sell', reduce_only: true, quantity_base: '20'})
    );
    expect(
      tooBig.risk.checks.find(c => c.check === 'mode.permitted')?.code
    ).toBe('LIVE_NOT_ARMED');
    expect(
      await failure(broker.amendOrder(working.order_id, {limit_price: '151'}))
    ).toEqual([403, 'LIVE_NOT_ARMED']);
    const closed = await broker.closePosition(
      position.position_id,
      {fraction: '1', order_type: 'market', preview_token: close.preview_token},
      'close-1'
    );
    expect(closed.risk.decision).toBe('ALLOW');
  });
});
