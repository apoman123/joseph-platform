/**
 * @fileoverview Tests for the HTTP execution client's account live arming
 * calls (paths, methods, bodies and contract errors).
 */
import {describe, expect, it, vi} from 'vitest';
import {ApiError, HttpExecutionClient} from '../src/client/http';

/** One recorded request. */
interface Call {
  /** Request URL. */
  url: string;
  /** Request options. */
  init: RequestInit;
}

/**
 * Builds a client whose fetch answers with a fixed status and body.
 * @param status HTTP status.
 * @param body JSON body.
 * @return Client and recorded calls.
 */
function clientAnswering(
  status: number,
  body: unknown
): {client: HttpExecutionClient; calls: Call[]} {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
    calls.push({url, init});
    return new Response(JSON.stringify(body), {status});
  });
  const client = new HttpExecutionClient({
    baseUrl: 'http://engine.test/',
    getToken: () => 'tok',
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  return {client, calls};
}

describe('HttpExecutionClient live arming', () => {
  it('arms an account with a POST carrying the step-up body', async () => {
    const {client, calls} = clientAnswering(200, {
      account_id: 'acct/1',
      live_armed_until_ms: 1_000,
    });
    const summary = await client.grantAccountLiveConsent('acct/1', {
      step_up_code: '123456',
      acknowledge_risk: true,
      duration_ms: 3_600_000,
    });
    expect(summary.live_armed_until_ms).toBe(1_000);
    expect(calls[0]?.url).toBe(
      'http://engine.test/v1/accounts/acct%2F1/live-consent'
    );
    expect(calls[0]?.init.method).toBe('POST');
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
      step_up_code: '123456',
      acknowledge_risk: true,
      duration_ms: 3_600_000,
    });
    expect(calls[0]?.init.headers).toMatchObject({
      Authorization: 'Bearer tok',
    });
  });

  it('disarms an account with a DELETE and no body', async () => {
    const {client, calls} = clientAnswering(200, {
      account_id: 'acct-1',
      live_armed_until_ms: null,
    });
    const summary = await client.revokeAccountLiveConsent('acct-1');
    expect(summary.live_armed_until_ms).toBeNull();
    expect(calls[0]?.url).toBe(
      'http://engine.test/v1/accounts/acct-1/live-consent'
    );
    expect(calls[0]?.init.method).toBe('DELETE');
    expect(calls[0]?.init.body).toBeUndefined();
  });

  it('surfaces a refused step-up code as a contract ApiError', async () => {
    const {client} = clientAnswering(403, {
      code: 'STEP_UP_REQUIRED',
      message: 'step-up verification failed',
      trace_id: 't',
      retryable: false,
      field_errors: [],
    });
    const failure = client.grantAccountLiveConsent('acct-1', {
      step_up_code: '000000',
      acknowledge_risk: true,
    });
    await expect(failure).rejects.toBeInstanceOf(ApiError);
    await expect(failure).rejects.toMatchObject({
      status: 403,
      body: {code: 'STEP_UP_REQUIRED'},
    });
  });
});
