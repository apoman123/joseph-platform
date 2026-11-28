/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO implementation of `StrategyClient`: the built-in
 * Strategy scripts as display data, synthetic DEMO backtests, in-memory
 * activations (AUTO_LIVE is always rejected) and fixture signals. DEMO has
 * no Python runtime; nothing here implements a strategy.
 */

import {
  ApiError,
  type AlertSubscription,
  type AlertDelivery,
  type AlertSubscriptionList,
  type ErrorCode,
  type ScriptResult,
  type ScriptRun,
  type ScriptVersion,
  type Signal,
  type SignalList,
  type SignalQuery,
  type StrategyActivation,
  type StrategyActivationInput,
  type StrategyActivationList,
  type StrategyClient,
  type StrategyScript,
  type StrategyScriptList,
} from '@joseph/contracts';
import {DEMO_BUILTIN_STRATEGIES, demoBacktest} from './demo-strategy-catalog';
import {createDemoSignals} from './demo-signals';

/** Options of the DEMO strategy client. */
export interface DemoStrategyOptions {
  /** Clock in epoch ms (UTC; defaults to `Date.now`). */
  now?: () => number;
  /** Signal fixtures, copied; defaults to `createDemoSignals(now())`. */
  signals?: Signal[];
}

/** Explanation of the AUTO_LIVE refusal, as the market engine words it. */
const AUTO_LIVE_REFUSAL =
  'AUTO_LIVE cannot be activated from the market engine. Create an execution-engine deployment for one account with limits, then give explicit scoped consent; live trading is disabled by default.';

/**
 * Builds an `ApiError` with a contract error body.
 * @param status HTTP status code.
 * @param code Contract error code.
 * @param message User-facing message.
 * @return Error for the caller to throw.
 */
function apiError(status: number, code: ErrorCode, message: string): ApiError {
  return new ApiError(status, {
    code,
    message,
    trace_id: 'demo',
    retryable: false,
    field_errors: [],
  });
}

/**
 * DEMO strategy service: built-in strategy descriptions, synthetic
 * backtests, in-memory activations and fixture signals. Nothing is
 * persisted.
 */
export class DemoStrategyClient implements StrategyClient {
  private readonly now: () => number;
  private readonly signals: Signal[];
  private readonly signalListeners = new Set<(signal: Signal) => void>();
  private readonly subscriptions = new Map<string, AlertSubscription>();
  private readonly owned = new Map<string, StrategyScript>();
  private readonly activations = new Map<string, StrategyActivation>();
  private sequence = 0;

  /**
   * Creates the client.
   * @param options Clock and signal fixtures.
   */
  constructor(options: DemoStrategyOptions = {}) {
    this.now = options.now ?? Date.now;
    this.signals = [...(options.signals ?? createDemoSignals(this.now()))];
  }

  /**
   * Subscribes to emitted signals (used by `DemoUserStream`).
   * @param listener Called with each signal `emitSignal` adds.
   * @return Unsubscribe function.
   */
  onSignal(listener: (signal: Signal) => void): () => void {
    this.signalListeners.add(listener);
    return () => {
      this.signalListeners.delete(listener);
    };
  }

  /**
   * Adds a DEMO signal and notifies stream listeners.
   * @param signal Signal, put first in the list.
   */
  emitSignal(signal: Signal): void {
    this.signals.unshift(signal);
    for (const listener of [...this.signalListeners]) {
      listener(signal);
    }
  }

  /**
   * Finds a built-in or DEMO-owned strategy.
   * @param scriptId Strategy id.
   * @return The strategy.
   * @throws `ApiError` (404 `NOT_FOUND`) for unknown ids.
   */
  private strategy(scriptId: string): StrategyScript {
    const found =
      DEMO_BUILTIN_STRATEGIES.find(s => s.script_id === scriptId) ??
      this.owned.get(scriptId);
    if (!found) {
      throw apiError(404, 'NOT_FOUND', `strategy ${scriptId} not found`);
    }
    return found;
  }

  /**
   * Lists the built-in strategies and DEMO copies.
   * @return Strategies; owned source cannot run in DEMO.
   */
  async listStrategyScripts(): Promise<StrategyScriptList> {
    return {
      scripts: [...DEMO_BUILTIN_STRATEGIES, ...this.owned.values()],
      runtime_enabled: false,
    };
  }

  /**
   * Explains where the source lives: DEMO has no Python runtime.
   * @param scriptId Strategy id.
   * @return A clearly labelled DEMO placeholder, never strategy code.
   */
  async getStrategySource(scriptId: string): Promise<string> {
    const strategy = this.strategy(scriptId);
    return `# DEMO: ${strategy.title} (${strategy.script_id}).\n# The public Python source is served by the market engine; connect it to read, copy and run this script.\n`;
  }

  /**
   * Records an in-memory DEMO copy of a strategy.
   * @param scriptId Strategy to copy.
   * @param newId New owned slug.
   * @return DEMO version 1.
   * @throws `ApiError` (422 `VALIDATION_FAILED`) for an invalid slug, (409
   *     `CONFLICT`) when it exists.
   */
  async copyStrategy(scriptId: string, newId: string): Promise<ScriptVersion> {
    const original = this.strategy(scriptId);
    if (!/^[a-z][a-z0-9-]{0,62}$/.test(newId)) {
      throw apiError(
        422,
        'VALIDATION_FAILED',
        'Script ids are lowercase letters, digits and dashes.'
      );
    }
    if (this.owned.has(newId)) {
      throw apiError(409, 'CONFLICT', `script ${newId} already exists`);
    }
    const title = `${original.title} (copy)`;
    this.owned.set(newId, {
      ...original,
      script_id: newId,
      origin: 'tenant',
      title,
      version: 1,
    });
    return {
      script_id: newId,
      kind: 'strategy',
      name: title,
      source: await this.getStrategySource(scriptId),
      overlay: true,
      defaults: {},
      libraries: {},
      requests: {},
      version: 1,
      source_hash: 'demo',
      created_at_ms: this.now(),
    };
  }

  /**
   * Builds a synthetic DEMO backtest; it says nothing about the strategy.
   * @param scriptId Strategy id.
   * @param request Instrument, timeframe and bar count.
   * @return DEMO-labelled result.
   */
  async backtestStrategy(
    scriptId: string,
    request: ScriptRun
  ): Promise<ScriptResult> {
    this.strategy(scriptId);
    return demoBacktest(scriptId, request, this.now());
  }

  /**
   * Lists DEMO activations, newest first.
   * @return Activations; nothing is evaluated or forwarded in DEMO.
   */
  async listActivations(): Promise<StrategyActivationList> {
    return {
      activations: [...this.activations.values()].sort(
        (a, b) => b.created_at_ms - a.created_at_ms
      ),
      runtime_enabled: false,
      forwarding_enabled: false,
    };
  }

  /**
   * Stores a DEMO activation with the market engine's rules.
   * @param request Activation request.
   * @return The ACTIVE activation.
   * @throws `ApiError` (409 `TRADE_MODE_DISABLED`) for AUTO_LIVE.
   */
  async createActivation(
    request: StrategyActivationInput
  ): Promise<StrategyActivation> {
    if (request.mode === 'AUTO_LIVE') {
      throw apiError(409, 'TRADE_MODE_DISABLED', AUTO_LIVE_REFUSAL);
    }
    const strategy = this.strategy(request.script_id);
    const now = this.now();
    this.sequence += 1;
    const activation: StrategyActivation = {
      ...request,
      mode: request.mode ?? 'ALERT_ONLY',
      channels: request.channels ?? ['UI'],
      inputs: request.inputs ?? {},
      history_bars: request.history_bars ?? 500,
      version: strategy.version,
      activation_id: `act-demo-${this.sequence}`,
      name: strategy.title,
      source_hash: 'demo',
      strategy_version_id: `${strategy.script_id}:v${strategy.version || 'demo'}`,
      state: 'ACTIVE',
      created_at_ms: now,
      updated_at_ms: now,
      last_checked_ms: null,
      last_error: null,
      emitted: 0,
      instruments: Object.fromEntries(
        request.instrument_ids.map(id => [
          id,
          {last_processed_ms: now, last_error: null},
        ])
      ),
    };
    this.activations.set(activation.activation_id, activation);
    return structuredClone(activation);
  }

  /**
   * Changes a DEMO activation's state.
   * @param activationId Activation id.
   * @param state New state.
   * @return Updated copy.
   * @throws `ApiError` (404 `NOT_FOUND`) for unknown ids.
   */
  private setState(
    activationId: string,
    state: StrategyActivation['state']
  ): StrategyActivation {
    const found = this.activations.get(activationId);
    if (!found) {
      throw apiError(404, 'NOT_FOUND', `activation ${activationId} not found`);
    }
    found.state = state;
    found.updated_at_ms = this.now();
    return structuredClone(found);
  }

  /**
   * Pauses a DEMO activation.
   * @param activationId Activation id.
   * @return The PAUSED activation.
   */
  async pauseActivation(activationId: string): Promise<StrategyActivation> {
    return this.setState(activationId, 'PAUSED');
  }

  /**
   * Resumes a DEMO activation.
   * @param activationId Activation id.
   * @return The ACTIVE activation.
   */
  async resumeActivation(activationId: string): Promise<StrategyActivation> {
    return this.setState(activationId, 'ACTIVE');
  }

  /**
   * Deletes a DEMO activation.
   * @param activationId Activation id.
   * @return Completion.
   */
  async deleteActivation(activationId: string): Promise<void> {
    this.activations.delete(activationId);
  }

  /**
   * Lists DEMO notification subscriptions. Only the in-app channel is
   * configured, so external channels are stored but never delivered.
   * @return Stored subscriptions and the configured channels (`UI` only).
   */
  async listAlertSubscriptions(): Promise<AlertSubscriptionList> {
    return {
      subscriptions: [...this.subscriptions.values()],
      configured_channels: ['UI'],
    };
  }

  /**
   * Stores a DEMO notification subscription.
   * @param subscription Subscription; replaces one with the same id.
   * @return Stored deep copy.
   * @throws `ApiError` (422 `VALIDATION_FAILED`) when the id is not 1-63
   *     lowercase letters, digits or dashes starting with a letter or digit.
   */
  async putAlertSubscription(
    subscription: AlertSubscription
  ): Promise<AlertSubscription> {
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(subscription.subscription_id)) {
      throw new ApiError(422, {
        code: 'VALIDATION_FAILED',
        message: 'Subscription id must be lowercase letters, digits or dashes.',
        trace_id: 'demo',
        retryable: false,
        field_errors: [],
      });
    }
    const stored = structuredClone(subscription);
    delete stored.web_push;
    this.subscriptions.set(stored.subscription_id, stored);
    return stored;
  }

  /**
   * Deletes an in-memory DEMO destination.
   * @param subscriptionId Public destination id.
   * @return Completion without external calls.
   */
  async deleteAlertSubscription(subscriptionId: string): Promise<void> {
    this.subscriptions.delete(subscriptionId);
  }

  /**
   * Explains that DEMO mode cannot deliver external test messages.
   * @param subscriptionId Destination id for the user-facing error.
   * @return Rejected promise; no simulated delivery is claimed.
   * @throws Error explaining that real engines must be connected.
   */
  async testAlertSubscription(subscriptionId: string): Promise<AlertDelivery> {
    throw new Error(
      `Connect the market engine to test ${subscriptionId}; DEMO mode does not send notifications.`
    );
  }

  /**
   * Rejects status queries because DEMO mode never sends notifications.
   * @param deliveryId Opaque identifier requested by the caller.
   * @return Rejected promise without a fabricated delivery.
   * @throws Error explaining that no external delivery exists.
   */
  async getAlertDelivery(deliveryId: string): Promise<AlertDelivery> {
    throw new Error(`No external delivery ${deliveryId} exists in DEMO mode.`);
  }

  /**
   * Lists signals, newest first.
   * @param query Strategy, kind and instrument filters, a bar-close window
   *     `[fromMs, toMs)` in epoch ms, and `limit` (default 100).
   * @return Matching signals by bar close, newest first, in one page (no
   *     cursor).
   */
  async listSignals(query: SignalQuery): Promise<SignalList> {
    const signals = this.signals
      .filter(
        s =>
          (!query.strategyId || s.strategy_id === query.strategyId) &&
          (!query.kinds || query.kinds.includes(s.kind)) &&
          (!query.instrumentId || s.instrument_id === query.instrumentId) &&
          (query.fromMs === undefined || s.bar_close_ms >= query.fromMs) &&
          (query.toMs === undefined || s.bar_close_ms < query.toMs)
      )
      .sort((a, b) => b.bar_close_ms - a.bar_close_ms)
      .slice(0, query.limit ?? 100);
    return {signals, next_cursor: null};
  }
}
