/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the resumable WebSocket stream client.
 */

import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import type {StreamEnvelope} from '../src/generated/contracts';
import {
  WebSocketStreamClient,
  channelKey,
  type WebSocketLike,
} from '../src/client/stream';

/** Fake socket that records sent frames and lets a test fire its events. */
class FakeSocket implements WebSocketLike {
  readyState = 0;
  sent: string[] = [];
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: {data: unknown}) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;

  /**
   * Records a frame.
   * @param data JSON text of a client message.
   */
  send(data: string): void {
    this.sent.push(data);
  }

  /** Simulates a close. */
  close(): void {
    this.readyState = 3;
    this.onclose?.({});
  }

  /** Simulates the open event. */
  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }

  /**
   * Delivers an envelope as a JSON text frame.
   * @param envelope Envelope fields to send.
   */
  deliver(envelope: Partial<StreamEnvelope>): void {
    this.onmessage?.({data: JSON.stringify(envelope)});
  }
}

/**
 * Builds a `candle.updated` envelope on the mock BTCUSD 5m candle channel.
 * @param sequence Sequence number; the event id is `e<sequence>`.
 * @param extra Fields that override the defaults.
 * @return Envelope.
 */
function envelope(
  sequence: number,
  extra: Partial<StreamEnvelope> = {}
): StreamEnvelope {
  return {
    type: 'candle.updated',
    event_id: `e${sequence}`,
    schema_version: 1,
    sequence,
    event_ts_ms: 1,
    channel: 'candles:mock:perp:BTCUSD:5m',
    payload: {},
    ...extra,
  };
}

describe('WebSocketStreamClient', () => {
  let sockets: FakeSocket[];
  let client: WebSocketStreamClient;
  const spec = {
    channel: 'candles' as const,
    instrument_id: 'mock:perp:BTCUSD',
    timeframe: '5m' as const,
  };

  beforeEach(() => {
    vi.useFakeTimers();
    sockets = [];
    client = new WebSocketStreamClient({
      url: 'ws://test/v1/stream/market',
      createSocket: () => {
        const socket = new FakeSocket();
        sockets.push(socket);
        return socket;
      },
      minBackoffMs: 100,
      maxBackoffMs: 1000,
      heartbeatMs: 1000,
      random: () => 0.5,
    });
  });

  afterEach(() => {
    client.close();
    vi.useRealTimers();
  });

  it('builds canonical channel keys', () => {
    expect(channelKey(spec)).toBe('candles:mock:perp:BTCUSD:5m');
    expect(channelKey({...spec, feed_mode: 'AUTO'})).toBe(channelKey(spec));
    expect(channelKey({...spec, feed_mode: 'NATIVE_WS'})).toBe(
      `${channelKey(spec)}:NATIVE_WS`
    );
  });

  it('retries a socket whose opening handshake never completes', () => {
    const statuses: string[] = [];
    client.onStatus(status => statuses.push(status));
    client.subscribe([spec], () => undefined);
    const first = sockets[0];
    vi.advanceTimersByTime(10_000);
    expect(first?.readyState).toBe(3);
    expect(statuses.at(-1)).toBe('RECONNECTING');
    vi.advanceTimersByTime(100);
    expect(sockets).toHaveLength(2);
    sockets[1]?.open();
    expect(statuses.at(-1)).toBe('OPEN');
    vi.advanceTimersByTime(100);
    expect(sockets).toHaveLength(2);
  });

  it('cancels the opening deadline when unsubscribed', () => {
    const unsubscribe = client.subscribe([spec], () => undefined);
    unsubscribe();
    vi.advanceTimersByTime(20_000);
    expect(sockets).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps explicit feed modes separate during routing and unsubscribe', () => {
    const auto = vi.fn();
    const native = vi.fn();
    const nativeSpec = {...spec, feed_mode: 'NATIVE_WS' as const};
    const removeNative = client.subscribe([nativeSpec], native);
    client.subscribe([spec], auto);
    sockets[0]?.open();
    expect(JSON.parse(sockets[0]?.sent[0] ?? '{}').channels).toHaveLength(2);
    sockets[0]?.deliver(
      envelope(1, {channel: `${channelKey(spec)}:NATIVE_WS`})
    );
    expect(native).toHaveBeenCalledTimes(1);
    expect(auto).not.toHaveBeenCalled();
    sockets[0]?.deliver(envelope(2));
    expect(auto).toHaveBeenCalledTimes(1);
    expect(native).toHaveBeenCalledTimes(1);
    removeNative();
    expect(JSON.parse(sockets[0]?.sent.at(-1) ?? '{}')).toMatchObject({
      action: 'unsubscribe',
      channels: [nativeSpec],
    });
  });

  it('subscribes on open and routes by channel', () => {
    const handler = vi.fn();
    client.subscribe([spec], handler);
    sockets[0]?.open();
    expect(JSON.parse(sockets[0]?.sent[0] ?? '{}')).toMatchObject({
      action: 'subscribe',
      channels: [spec],
    });
    sockets[0]?.deliver(envelope(1));
    sockets[0]?.deliver(envelope(2, {channel: 'candles:mock:perp:ETHUSD:5m'}));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('drops duplicate redeliveries by event id', () => {
    const handler = vi.fn();
    client.subscribe([spec], handler);
    sockets[0]?.open();
    sockets[0]?.deliver(envelope(1));
    sockets[0]?.deliver(envelope(1));
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('reconnects with backoff and resumes from the last sequence', () => {
    const statuses: string[] = [];
    client.onStatus(status => statuses.push(status));
    client.subscribe([spec], () => undefined);
    sockets[0]?.open();
    sockets[0]?.deliver(envelope(41));
    sockets[0]?.close();
    expect(statuses).toContain('RECONNECTING');
    vi.advanceTimersByTime(100);
    expect(sockets).toHaveLength(2);
    sockets[1]?.open();
    expect(JSON.parse(sockets[1]?.sent[0] ?? '{}')).toMatchObject({
      action: 'subscribe',
      last_sequence: 41,
    });
    expect(statuses.at(-1)).toBe('OPEN');
  });

  it('forwards snapshot_required to affected subscribers', () => {
    const handler = vi.fn();
    const other = vi.fn();
    client.subscribe([spec], handler);
    client.subscribe([{channel: 'feed_health'}], other);
    sockets[0]?.open();
    sockets[0]?.deliver(
      envelope(3, {
        type: 'snapshot_required',
        channel: null,
        payload: {
          reason: 'retention',
          channels: ['candles:mock:perp:BTCUSD:5m'],
        },
      })
    );
    expect(handler).toHaveBeenCalledWith(
      expect.objectContaining({type: 'snapshot_required'})
    );
    expect(other).not.toHaveBeenCalled();
  });

  it('sends heartbeats and recycles silent connections', () => {
    client.subscribe([spec], () => undefined);
    sockets[0]?.open();
    vi.advanceTimersByTime(1000);
    expect(sockets[0]?.sent.some(frame => frame.includes('ping'))).toBe(true);
    vi.advanceTimersByTime(2500);
    vi.advanceTimersByTime(200);
    expect(sockets.length).toBeGreaterThan(1);
  });

  it('closes the socket when the last subscriber leaves', () => {
    const unsubscribe = client.subscribe([spec], () => undefined);
    sockets[0]?.open();
    unsubscribe();
    expect(sockets[0]?.sent.some(f => f.includes('unsubscribe'))).toBe(true);
    expect(sockets[0]?.readyState).toBe(3);
  });

  it('isolates handler errors', () => {
    const good = vi.fn();
    client.subscribe([spec], () => {
      throw new Error('bad');
    });
    client.subscribe([spec], good);
    sockets[0]?.open();
    sockets[0]?.deliver(envelope(1));
    expect(good).toHaveBeenCalledOnce();
  });
});
