/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Resumable WebSocket implementation of `StreamClient`.
 *
 * Reconnects with jittered exponential backoff, resubscribes with the last
 * seen sequence, deduplicates redelivered envelopes by `event_id`, sends
 * heartbeats and forwards `snapshot_required` so consumers refetch REST
 * snapshots. It never assumes delivery is complete or ordered.
 */

import type {
  ChannelSpec,
  ClientMessage,
  StreamEnvelope,
} from '../generated/contracts';
import type {StreamClient, StreamHandler, StreamStatus} from './ports';

/** Minimal WebSocket surface (browser `WebSocket` satisfies it). */
export interface WebSocketLike {
  /** Ready state (1 = open). */
  readonly readyState: number;
  /**
   * Sends text.
   * @param data Text frame.
   */
  send(data: string): void;
  /**
   * Closes the socket.
   * @param code Close code.
   * @param reason Reason text.
   */
  close(code?: number, reason?: string): void;
  /** Open handler. */
  onopen: ((event: unknown) => void) | null;
  /** Message handler. */
  onmessage: ((event: {data: unknown}) => void) | null;
  /** Close handler. */
  onclose: ((event: unknown) => void) | null;
  /** Error handler. */
  onerror: ((event: unknown) => void) | null;
}

/** Creates sockets (injectable for tests). */
export type WebSocketFactory = (url: string) => WebSocketLike;

/** Options for `WebSocketStreamClient`. */
export interface StreamClientOptions {
  /** Stream URL, or a function returning it (e.g. to append a token). */
  url: string | (() => string);
  /** Socket factory; defaults to the global `WebSocket`. */
  createSocket?: WebSocketFactory;
  /**
   * Backoff base of the first reconnect, in ms (default 500). The base
   * doubles with each failed attempt; the actual delay is drawn between half
   * of it and all of it.
   */
  minBackoffMs?: number;
  /** Cap on the backoff base, in ms (default 15 000). */
  maxBackoffMs?: number;
  /**
   * Ping interval in ms (default 15 000). A connection that receives
   * nothing for more than two intervals is closed and reconnected.
   */
  heartbeatMs?: number;
  /** Opening handshake deadline in milliseconds (default 10,000). */
  connectTimeoutMs?: number;
  /** Random source in [0, 1) for jitter. */
  random?: () => number;
}

/** `WebSocket.OPEN`, without reading the global (absent on the server). */
const OPEN = 1;
/** Number of recent event ids remembered for deduplication. */
const DEDUPE_WINDOW = 2048;

/** One active subscription. */
interface Subscription {
  /** Channel specs. */
  channels: readonly ChannelSpec[];
  /** Channel keys derived from the specs. */
  keys: ReadonlySet<string>;
  /** Receives the envelopes of these channels. */
  handler: StreamHandler;
}

/**
 * Builds the canonical channel key, e.g. `candles:binance:perp:BTCUSDT:5m`.
 * @param spec Channel spec.
 * @return Channel key.
 */
export function channelKey(spec: ChannelSpec): string {
  const parts = [
    spec.channel,
    spec.instrument_id,
    spec.timeframe,
    spec.account_id,
  ];
  // The server gives AUTO the unsuffixed key and isolates explicit modes.
  if (
    spec.channel === 'candles' &&
    spec.feed_mode &&
    spec.feed_mode !== 'AUTO'
  ) {
    parts.push(spec.feed_mode);
  }
  return parts.filter((part): part is string => Boolean(part)).join(':');
}

/** Resumable WebSocket stream client. */
export class WebSocketStreamClient implements StreamClient {
  private socket: WebSocketLike | null = null;
  private readonly subscriptions = new Set<Subscription>();
  private readonly statusHandlers = new Set<(status: StreamStatus) => void>();
  private readonly seenEventIds: string[] = [];
  private readonly seenSet = new Set<string>();
  private status: StreamStatus = 'CLOSED';
  private attempt = 0;
  private lastSequence: number | null = null;
  private lastMessageAt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;

  /**
   * Creates the client; it connects lazily on the first subscription.
   * @param options Connection options.
   */
  constructor(private readonly options: StreamClientOptions) {}

  /**
   * Subscribes to channels, connecting first if there is no socket yet.
   * @param channels Channel specs.
   * @param handler Envelope handler.
   * @return Unsubscribe function. It unsubscribes the channels no other
   *     subscription uses and closes the connection after the last one.
   */
  subscribe(
    channels: readonly ChannelSpec[],
    handler: StreamHandler
  ): () => void {
    const subscription: Subscription = {
      channels,
      keys: new Set(channels.map(channelKey)),
      handler,
    };
    this.subscriptions.add(subscription);
    this.closed = false;
    if (this.socket?.readyState === OPEN) {
      this.send({action: 'subscribe', channels: [...channels]});
    } else if (!this.socket && !this.reconnectTimer) {
      this.connect();
    }
    return () => {
      if (!this.subscriptions.delete(subscription)) return;
      const stillUsed = new Set(
        [...this.subscriptions].flatMap(sub => [...sub.keys])
      );
      const orphaned = channels.filter(
        spec => !stillUsed.has(channelKey(spec))
      );
      if (orphaned.length > 0 && this.socket?.readyState === OPEN) {
        this.send({action: 'unsubscribe', channels: orphaned});
      }
      if (this.subscriptions.size === 0) this.shutdown();
    };
  }

  /**
   * Observes connection status (invoked immediately with the current one).
   * @param handler Status handler.
   * @return Unsubscribe function.
   */
  onStatus(handler: (status: StreamStatus) => void): () => void {
    this.statusHandlers.add(handler);
    handler(this.status);
    return () => {
      this.statusHandlers.delete(handler);
    };
  }

  /** Closes the connection and stops reconnecting. */
  close(): void {
    this.closed = true;
    this.subscriptions.clear();
    this.shutdown();
  }

  /** Tears down the socket and timers and reports `CLOSED`. */
  private shutdown(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
    this.stopHeartbeat();
    this.stopConnectTimer();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      // Detach the handlers first so this close does not schedule a
      // reconnect.
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.close(1000, 'client closed');
    }
    this.setStatus('CLOSED');
  }

  /** Opens a socket and wires handlers. */
  private connect(): void {
    const url =
      typeof this.options.url === 'function'
        ? this.options.url()
        : this.options.url;
    const factory =
      this.options.createSocket ??
      // The DOM handlers take narrower event types than `WebSocketLike`
      // declares, so the structural check fails although every use is safe.
      ((target: string) => new WebSocket(target) as unknown as WebSocketLike);
    this.setStatus(this.attempt === 0 ? 'CONNECTING' : 'RECONNECTING');
    let socket: WebSocketLike;
    try {
      socket = factory(url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    this.connectTimer = setTimeout(() => {
      if (this.socket !== socket) return;
      // A stalled handshake never starts the heartbeat. Detach this socket
      // and retry without waiting for its close event to arrive.
      this.socket = null;
      this.stopConnectTimer();
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.close(4000, 'connection timeout');
      this.scheduleReconnect();
    }, this.options.connectTimeoutMs ?? 10_000);
    socket.onopen = () => {
      this.stopConnectTimer();
      // Resume from the last sequence seen: the server replays from there, or
      // sends `snapshot_required` when that is beyond its retention window.
      this.attempt = 0;
      this.lastMessageAt = Date.now();
      this.setStatus('OPEN');
      const channels = [...this.subscriptions].flatMap(sub => sub.channels);
      if (channels.length > 0) {
        this.send({
          action: 'subscribe',
          channels: dedupeChannels(channels),
          last_sequence: this.lastSequence,
        });
      }
      this.startHeartbeat();
    };
    socket.onmessage = event => {
      this.lastMessageAt = Date.now();
      this.handleMessage(event.data);
    };
    socket.onerror = () => {
      // The close handler drives reconnection.
    };
    socket.onclose = () => {
      // A socket that was replaced or shut down must not touch the state.
      if (this.socket !== socket) return;
      this.socket = null;
      this.stopConnectTimer();
      this.stopHeartbeat();
      if (!this.closed && this.subscriptions.size > 0) {
        this.scheduleReconnect();
      } else {
        this.setStatus('CLOSED');
      }
    };
  }

  /** Clears the pending opening deadline after connection or shutdown. */
  private stopConnectTimer(): void {
    if (this.connectTimer !== null) clearTimeout(this.connectTimer);
    this.connectTimer = null;
  }

  /** Schedules a reconnect with jittered exponential backoff. */
  private scheduleReconnect(): void {
    if (this.closed || this.reconnectTimer) return;
    const min = this.options.minBackoffMs ?? 500;
    const max = this.options.maxBackoffMs ?? 15_000;
    const random = this.options.random ?? Math.random;
    const base = Math.min(max, min * 2 ** this.attempt);
    // Jitter spreads the reconnects of many clients after a server restart.
    const delay = Math.round(base / 2 + random() * (base / 2));
    this.attempt += 1;
    this.setStatus('RECONNECTING');
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  /** Starts the heartbeat timer. */
  private startHeartbeat(): void {
    this.stopHeartbeat();
    const interval = this.options.heartbeatMs ?? 15_000;
    this.heartbeatTimer = setInterval(() => {
      if (Date.now() - this.lastMessageAt > interval * 2) {
        // Silent connection: recycle it so reconnect + resume can run.
        this.socket?.close(4000, 'heartbeat timeout');
        return;
      }
      this.send({action: 'ping'});
    }, interval);
  }

  /** Stops the heartbeat timer. */
  private stopHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  /**
   * Sends a client message if the socket is open.
   * @param message Client message.
   */
  private send(message: ClientMessage): void {
    if (this.socket?.readyState !== OPEN) return;
    try {
      this.socket.send(JSON.stringify(message));
    } catch {
      // A failed send surfaces as a close event.
    }
  }

  /**
   * Parses and routes one frame.
   * @param data Raw frame data.
   */
  private handleMessage(data: unknown): void {
    let envelope: StreamEnvelope;
    try {
      // Only `type` is checked below; the engines send contract envelopes.
      envelope = JSON.parse(String(data)) as StreamEnvelope;
    } catch {
      // A frame that is not JSON is dropped.
      return;
    }
    if (!envelope || typeof envelope.type !== 'string') return;
    if (envelope.type === 'heartbeat') return;
    if (envelope.event_id) {
      if (this.seenSet.has(envelope.event_id)) return;
      this.remember(envelope.event_id);
    }
    // Frames may arrive out of order; resume from the highest sequence.
    if (typeof envelope.sequence === 'number') {
      this.lastSequence = Math.max(this.lastSequence ?? 0, envelope.sequence);
    }
    for (const subscription of [...this.subscriptions]) {
      if (matches(envelope, subscription.keys)) {
        try {
          subscription.handler(envelope);
        } catch {
          // Handler errors must not stop delivery to other subscribers.
        }
      }
    }
  }

  /**
   * Records an event id for deduplication.
   * @param eventId Event id.
   */
  private remember(eventId: string): void {
    this.seenEventIds.push(eventId);
    this.seenSet.add(eventId);
    if (this.seenEventIds.length > DEDUPE_WINDOW) {
      const evicted = this.seenEventIds.shift();
      if (evicted) this.seenSet.delete(evicted);
    }
  }

  /**
   * Publishes a status change.
   * @param status New status.
   */
  private setStatus(status: StreamStatus): void {
    if (this.status === status) return;
    this.status = status;
    for (const handler of [...this.statusHandlers]) {
      handler(status);
    }
  }
}

/**
 * Returns whether an envelope belongs to a subscription.
 * @param envelope Envelope.
 * @param keys Subscription channel keys.
 * @return True for its own channels, for a channel-less envelope, and for a
 *     `snapshot_required` that lists one of its channels or none.
 */
function matches(envelope: StreamEnvelope, keys: ReadonlySet<string>): boolean {
  if (envelope.type === 'snapshot_required') {
    // `channels` is checked before use; entries are only compared with
    // `Set.has`, so a non-string entry never matches.
    const payload = envelope.payload as {channels?: unknown};
    const listed = Array.isArray(payload.channels)
      ? (payload.channels as string[])
      : [];
    return listed.length === 0 || listed.some(key => keys.has(key));
  }
  if (!envelope.channel) return true;
  if (keys.has(envelope.channel)) return true;
  // Candle keys identify a complete series and mode, not a channel prefix.
  if (envelope.channel.startsWith('candles:')) return false;
  // Account- or tenant-wide channels (e.g. `orders`) match by prefix.
  return [...keys].some(
    key =>
      envelope.channel === key.split(':')[0] ||
      (envelope.channel?.startsWith(`${key}:`) ?? false)
  );
}

/**
 * Removes duplicate channel specs.
 * @param channels Specs, possibly repeated.
 * @return One spec per channel key, the last one given for each.
 */
function dedupeChannels(channels: readonly ChannelSpec[]): ChannelSpec[] {
  const seen = new Map<string, ChannelSpec>();
  for (const spec of channels) {
    seen.set(channelKey(spec), spec);
  }
  return [...seen.values()];
}
