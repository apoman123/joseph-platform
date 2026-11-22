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
   * @param listener Listener.
   * @return Unsubscribe function.
   */
  onSignal(listener: (signal: Signal) => void): () => void;
}

/** Channel used by each event type. */
const CHANNEL_OF: Readonly<Partial<Record<StreamEventType, ChannelName>>> = {
  'order.updated': 'orders',
  'fill.created': 'fills',
  'position.updated': 'positions',
  'risk.kill_switch': 'risk',
  'account.updated': 'account',
  'signal.created': 'signals',
  'signal.updated': 'signals',
};

/** DEMO implementation of the authenticated user stream. */
export class DemoUserStream implements StreamClient {
  private sequence = 0;
  private status: StreamStatus = 'OPEN';
  private readonly subscriptions = new Set<{
    channels: ReadonlySet<ChannelName>;
    handler: StreamHandler;
  }>();
  private readonly statusListeners = new Set<(status: StreamStatus) => void>();
  private readonly disposers: (() => void)[] = [];

  /**
   * Creates the stream.
   * @param broker DEMO broker to observe.
   * @param signals Optional DEMO signal source.
   * @param now Clock (UTC ms).
   */
  constructor(
    broker: DemoExecutionClient,
    signals: DemoSignalSource | null = null,
    private readonly now: () => number = Date.now
  ) {
    this.disposers.push(
      broker.onEvent(event => this.publish(event.type, event.payload))
    );
    if (signals)
      this.disposers.push(
        signals.onSignal(signal => this.publish('signal.created', signal))
      );
  }

  /**
   * Publishes an envelope to matching subscribers.
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
   * @param channels Channel specs.
   * @param handler Envelope handler.
   * @return Unsubscribe function.
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
   * @return Unsubscribe function.
   */
  onStatus(handler: (status: StreamStatus) => void): () => void {
    this.statusListeners.add(handler);
    handler(this.status);
    return () => {
      this.statusListeners.delete(handler);
    };
  }

  /** Closes the stream. */
  close(): void {
    this.status = 'CLOSED';
    for (const dispose of this.disposers.splice(0)) dispose();
    for (const listener of [...this.statusListeners]) listener('CLOSED');
    this.subscriptions.clear();
  }
}
