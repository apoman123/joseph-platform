/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the HTTP execution client's account live arming
 * calls (paths, methods, bodies and contract errors).
 */

import {afterEach, describe, expect, it, vi} from 'vitest';
import {
  ApiError,
  HttpExecutionClient,
  HttpMarketDataClient,
  HttpTransport,
} from '../src/client/http';

/**
 * Models a stalled fetch or response body that honors cancellation.
 * @param signal Optional request signal.
 * @return Pending promise, rejected with the abort reason on cancellation.
 */
function untilAborted(signal?: AbortSignal | null): Promise<never> {
  return new Promise((_resolve, reject) => {
    const cancel = () => {
      reject(
        signal?.reason instanceof Error
          ? signal.reason
          : new Error('Request aborted')
      );
    };
    if (signal?.aborted) cancel();
    else signal?.addEventListener('abort', cancel, {once: true});
  });
}

describe('HTTP read lifecycle', () => {
  afterEach(() => vi.useRealTimers());

  it.each(['headers', 'body'])(
    'ends a stalled GET while waiting for %s',
    async stage => {
      vi.useFakeTimers();
      const fetchImpl = vi.fn<typeof fetch>(async (_url, init) => {
        if (stage === 'headers') return untilAborted(init?.signal);
        const response = new Response('{}');
        vi.spyOn(response, 'text').mockImplementation(() =>
          untilAborted(init?.signal)
        );
        return response;
      });
      const http = new HttpTransport({
        baseUrl: 'http://engine.test',
        readTimeoutMs: 100,
        fetchImpl,
      });
      const failure = expect(
        http.request('GET', '/v1/candles')
      ).rejects.toThrow('GET /v1/candles timed out');
      await vi.advanceTimersByTimeAsync(100);
      await failure;
      expect(fetchImpl).toHaveBeenCalledTimes(1);
      expect(vi.getTimerCount()).toBe(0);
    }
  );

  it('cancels obsolete candle requests without adding the signal to the URL', async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn<typeof fetch>(async (_url, init) =>
      untilAborted(init?.signal)
    );
    const client = new HttpMarketDataClient({
      baseUrl: 'http://engine.test',
      fetchImpl,
    });
    const failure = expect(
      client.getCandles({
        instrumentId: 'nasdaq:stock:AAPL',
        timeframe: '5m',
        signal: controller.signal,
      })
    ).rejects.toThrow('chart changed');
    controller.abort(new Error('chart changed'));
    await failure;
    expect(String(fetchImpl.mock.calls[0]?.[0])).not.toContain('signal');
  });

  it('clears deadlines after success and does not time out trade mutations', async () => {
    vi.useFakeTimers();
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('{}'))
      .mockImplementation(async (_url, init) => {
        await new Promise(resolve => setTimeout(resolve, 200));
        expect(init?.signal?.aborted).toBe(false);
        return new Response('{}');
      });
    const http = new HttpTransport({
      baseUrl: 'http://engine.test',
      readTimeoutMs: 100,
      fetchImpl,
    });
    await http.request('GET', '/v1/candles');
    expect(vi.getTimerCount()).toBe(0);
    const mutation = http.request('POST', '/v1/orders');
    await vi.advanceTimersByTimeAsync(200);
    await expect(mutation).resolves.toEqual({});
  });
});

/** One recorded request. */
interface Call {
  /** Request URL. */
  url: string;
  /** Request options. */
  init: RequestInit;
}

/**
 * Builds a client whose fetch answers with a fixed status and body.
 * @param status HTTP status of every response.
 * @param body Value every response carries as JSON.
 * @return Client and the requests it made, in order.
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
    // The mock covers the one call shape the client uses, not every
    // overload of `fetch`.
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
