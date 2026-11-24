/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the ticket state machine and controller.
 */

import {
  ApiError,
  Decimal,
  type ExecutionClient,
  type OrderIntentRequest,
  type SubmitIntentResponse,
} from '@joseph/contracts';
import {describe, expect, it, vi} from 'vitest';
import {defaultTicketInputs} from './intent-builder';
import {
  TicketController,
  canConfirm,
  initialTicketState,
  modeAvailability,
  ticketReducer,
} from './ticket';
import {BTC, account, preview} from './testing/fixtures';

const REQUEST: OrderIntentRequest = {
  account_id: 'acct-paper-demo',
  instrument_id: 'mock:perp:BTCUSD',
  side: 'buy',
  position_effect: 'open',
  order_type: 'limit',
  quantity_base: '0.01',
  limit_price: '95000',
  time_in_force: 'GTC',
  reduce_only: false,
  mode: 'PAPER',
  source: 'MANUAL_TICKET',
};

/**
 * Builds a submit response for a request.
 * @param request Request.
 * @return Response.
 */
function response(request: OrderIntentRequest): SubmitIntentResponse {
  return {
    intent: {
      intent_id: 'i',
      tenant_id: 't',
      user_id: 'u',
      idempotency_key: 'k',
      client_order_id: 'c',
      created_at_ms: 1,
      request,
    },
    risk: {
      decision: 'ALLOW',
      checks: [],
      policy_version: 'p',
      evaluated_at_ms: 1,
    },
    orders: [],
    replayed: false,
  };
}

/**
 * Builds a fake execution client with spies.
 * @param overrides Method overrides.
 * @return Client.
 */
function fakeClient(overrides: Partial<ExecutionClient> = {}): ExecutionClient {
  return {
    previewOrder: vi.fn(async (req: OrderIntentRequest) => preview(req)),
    submitIntent: vi.fn(async (req: OrderIntentRequest) => response(req)),
    ...overrides,
  } as unknown as ExecutionClient;
}

describe('ticketReducer', () => {
  const base = initialTicketState(
    defaultTicketInputs(BTC.instrument_id, 'acct-paper-demo')
  );

  it('invalidates the preview on any edit', () => {
    let state = ticketReducer(base, {type: 'previewRequested'});
    state = ticketReducer(state, {
      type: 'previewSucceeded',
      preview: preview(REQUEST),
      revision: state.inputRevision,
    });
    expect(state.phase).toBe('PREVIEWED');
    state = ticketReducer(state, {type: 'edit', patch: {quantity: '0.02'}});
    expect(state.phase).toBe('DRAFT');
    expect(state.preview).toBeNull();
  });

  it('ignores a preview for stale inputs', () => {
    let state = ticketReducer(base, {type: 'previewRequested'});
    const revision = state.inputRevision;
    state = ticketReducer(state, {type: 'edit', patch: {quantity: '1'}});
    const after = ticketReducer(state, {
      type: 'previewSucceeded',
      preview: preview(REQUEST),
      revision,
    });
    expect(after).toBe(state);
  });

  it('requires an allowed, unexpired preview to confirm', () => {
    let state = ticketReducer(base, {type: 'previewRequested'});
    state = ticketReducer(state, {
      type: 'previewSucceeded',
      preview: preview(REQUEST, {allowed: false}),
      revision: state.inputRevision,
    });
    expect(canConfirm(state, 0)).toBe(false);
    expect(
      ticketReducer(state, {type: 'confirm', nowMs: 0, idempotencyKey: 'k'})
    ).toBe(state);
  });

  it('sends an expired preview back to draft', () => {
    let state = ticketReducer(base, {type: 'previewRequested'});
    state = ticketReducer(state, {
      type: 'previewSucceeded',
      preview: preview(REQUEST),
      revision: state.inputRevision,
    });
    state = ticketReducer(state, {
      type: 'confirm',
      nowMs: 20_000,
      idempotencyKey: 'k',
    });
    expect(state.phase).toBe('DRAFT');
    expect(state.error?.code).toBe('PREVIEW_EXPIRED');
  });

  it('cannot be edited while submitting', () => {
    let state = ticketReducer(base, {type: 'previewRequested'});
    state = ticketReducer(state, {
      type: 'previewSucceeded',
      preview: preview(REQUEST),
      revision: state.inputRevision,
    });
    state = ticketReducer(state, {
      type: 'confirm',
      nowMs: 0,
      idempotencyKey: 'k',
    });
    state = ticketReducer(state, {type: 'submitStarted'});
    expect(ticketReducer(state, {type: 'edit', patch: {quantity: '9'}})).toBe(
      state
    );
    expect(
      ticketReducer(state, {type: 'confirm', nowMs: 0, idempotencyKey: 'other'})
    ).toBe(state);
  });
});

describe('TicketController', () => {
  /**
   * Creates a controller with valid BTC inputs.
   * @param client Execution client.
   * @param keys Key factory.
   * @return Controller.
   */
  function controller(
    client: ExecutionClient,
    keys: () => string
  ): TicketController {
    const ctl = new TicketController(
      {
        ...defaultTicketInputs(BTC.instrument_id, 'acct-paper-demo'),
        quantity: '0.01',
        limitPrice: '95000',
      },
      {client, now: () => 0, newKey: keys}
    );
    ctl.setMarket(BTC, Decimal.parse('95000'));
    return ctl;
  }

  it('submits exactly once on a double click', async () => {
    const client = fakeClient();
    let n = 0;
    const ctl = controller(client, () => `key-${(n += 1)}`);
    await ctl.preview();
    await Promise.all([ctl.confirm(), ctl.confirm()]);
    expect(client.submitIntent).toHaveBeenCalledTimes(1);
    expect(ctl.get().phase).toBe('SUBMITTED');
    expect(
      vi.mocked(client.submitIntent).mock.calls[0]?.[0].preview_token
    ).toBe('tok-1');
  });

  it('reuses the idempotency key when retrying an uncertain submit', async () => {
    const submit = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('network down'))
      .mockImplementation(async (req: OrderIntentRequest) => response(req));
    const client = fakeClient({submitIntent: submit});
    let n = 0;
    const ctl = controller(client, () => `key-${(n += 1)}`);
    await ctl.preview();
    await ctl.confirm();
    expect(ctl.get().phase).toBe('FAILED');
    await ctl.confirm();
    expect(submit).toHaveBeenCalledTimes(2);
    expect(submit.mock.calls[0]?.[1]).toBe(submit.mock.calls[1]?.[1]);
    expect(ctl.get().phase).toBe('SUBMITTED');
  });

  it('drops the key after a definitive rejection', async () => {
    const reject = new ApiError(422, {
      code: 'RISK_LIMIT',
      message: 'Too large',
      trace_id: 't',
      retryable: false,
      field_errors: [],
    });
    const client = fakeClient({
      submitIntent: vi.fn().mockRejectedValue(reject),
    });
    const ctl = controller(client, () => 'key');
    await ctl.preview();
    await ctl.confirm();
    expect(ctl.get().error?.code).toBe('RISK_LIMIT');
    expect(ctl.get().idempotencyKey).toBeNull();
    await ctl.confirm();
    expect(client.submitIntent).toHaveBeenCalledTimes(1);
  });

  it('does not preview invalid inputs', async () => {
    const client = fakeClient();
    const ctl = controller(client, () => 'k');
    ctl.edit({quantity: ''});
    await ctl.preview();
    expect(client.previewOrder).not.toHaveBeenCalled();
  });
});

describe('modeAvailability', () => {
  it('disables LIVE unless the server lists it', () => {
    const modes = modeAvailability(account());
    expect(modes.find(m => m.mode === 'PAPER')?.available).toBe(true);
    const live = modes.find(m => m.mode === 'LIVE');
    expect(live?.available).toBe(false);
    expect(live?.reason).toMatch(/not enabled/);
  });

  it('disables LIVE when the adapter is unavailable', () => {
    const acct = account({modes: ['PAPER', 'LIVE']});
    acct.capabilities = {...acct.capabilities, status: 'TRADING_UNAVAILABLE'};
    expect(modeAvailability(acct).find(m => m.mode === 'LIVE')?.available).toBe(
      false
    );
  });

  it('requires an account', () => {
    expect(modeAvailability(null).every(m => !m.available)).toBe(true);
  });
});
