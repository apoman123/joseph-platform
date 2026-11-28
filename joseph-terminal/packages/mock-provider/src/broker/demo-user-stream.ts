/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO user stream: turns DEMO broker and strategy events
 * into contract `StreamEnvelope`s with monotonically increasing sequences.
 */

import type {
  ChannelName,
  ChannelSpec,
  Signal,
  StreamClient,
  StreamEnvelope,
  StreamEventType,
  StreamHandler,
  StreamStatus,
} from '@joseph/contracts';
import type {
  DemoBrokerEvent,
  DemoExecutionClient,
} from './demo-execution-client';

/** Source of DEMO signals (implemented by `DemoStrategyClient`). */
export interface DemoSignalSource {
  /**
   * Subscribes to newly emitted signals.
   * @param listener Called for every new signal.
   * @return Function that removes the listener.
   */
  onSignal(listener: (signal: Signal) => void): () => void;
}

/** Channel of each event type the stream publishes; others are dropped. */
const CHANNEL_OF: Readonly<Partial<Record<StreamEventType, ChannelName>>> = {
  'order.updated': 'orders',
  'fill.created': 'fills',
  'position.updated': 'positions',
  'risk.kill_switch': 'risk',
  'account.updated': 'account',
  'signal.created': 'signals',
};

/**
 * DEMO implementation of the authenticated user stream. It is open from
 * construction until `close`; it never reconnects or replays.
 */
export class DemoUserStream implements StreamClient {
  /** Sequence of the last envelope, shared by every channel. */
  private sequence = 0;
  private status: StreamStatus = 'OPEN';
  private readonly subscriptions = new Set<{
    channels: ReadonlySet<ChannelName>;
    handler: StreamHandler;
  }>();
  private readonly statusListeners = new Set<(status: StreamStatus) => void>();
  private readonly disposers: Array<() => void> = [];

  /**
   * Creates the stream and starts observing its sources.
   * @param broker DEMO broker to observe.
   * @param signals DEMO signal source, or null for none.
   * @param now Clock in epoch milliseconds (UTC), stamped on envelopes.
   */
  constructor(
    broker: DemoExecutionClient,
    signals: DemoSignalSource | null = null,
    private readonly now: () => number = Date.now
  ) {
    this.disposers.push(
      broker.onEvent(event => void this.publish(event.type, event.payload))
    );
    if (signals) {
      this.disposers.push(
        signals.onSignal(signal => void this.publish('signal.created', signal))
      );
    }
  }

  /**
   * Publishes an envelope to the subscribers of the event's channel.
   * Does nothing once closed or for an event type without a channel.
   * @param type Event type.
   * @param payload Contract payload.
   */
  private publish(
    type: DemoBrokerEvent['type'] | 'signal.created',
    payload: object
  ): void {
    if (this.status === 'CLOSED') return;
    const channel = CHANNEL_OF[type];
    if (!channel) return;
    this.sequence += 1;
    const envelope: StreamEnvelope = {
      type,
      event_id: `demo-ev-${this.sequence}`,
      schema_version: 1,
      sequence: this.sequence,
      event_ts_ms: this.now(),
      correlation_id: null,
      channel,
      payload,
    };
    for (const subscription of [...this.subscriptions]) {
      if (subscription.channels.has(channel)) subscription.handler(envelope);
    }
  }

  /**
   * Subscribes to channels.
   * @param channels Channel specs; only the channel names are matched and
   *     other spec fields are ignored.
   * @param handler Envelope handler, called synchronously.
   * @return Function that removes the subscription.
   */
  subscribe(
    channels: readonly ChannelSpec[],
    handler: StreamHandler
  ): () => void {
    const entry = {channels: new Set(channels.map(c => c.channel)), handler};
    this.subscriptions.add(entry);
    return () => {
      this.subscriptions.delete(entry);
    };
  }

  /**
   * Observes connection status; reports the current status immediately.
   * @param handler Status handler.
   * @return Function that removes the handler.
   */
  onStatus(handler: (status: StreamStatus) => void): () => void {
    this.statusListeners.add(handler);
    handler(this.status);
    return () => {
      this.statusListeners.delete(handler);
    };
  }

  /**
   * Closes the stream: detaches from its sources, reports `CLOSED` and drops
   * every subscription.
   */
  close(): void {
    this.status = 'CLOSED';
    for (const dispose of this.disposers.splice(0)) {
      dispose();
    }
    for (const listener of [...this.statusListeners]) {
      listener('CLOSED');
    }
    this.subscriptions.clear();
  }
}
