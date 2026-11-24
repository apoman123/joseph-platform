/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO implementation of `StrategyClient`: in-memory immutable
 * versions, activation rules (AUTO_LIVE is always rejected), a canned DEMO
 * backtest report and fixture signals.
 */

import {
  ApiError,
  type AlertSubscription,
  type AlertSubscriptionList,
  TIMEFRAME_MS,
  type ActivateRequest,
  type BacktestReport,
  type BacktestRequest,
  type ErrorCode,
  type Signal,
  type SignalList,
  type SignalQuery,
  type StrategyClient,
  type StrategyDefinition,
  type StrategyVersion,
  type StrategyVersionList,
  type ValidationResult,
} from '@joseph/contracts';
import {demoCalendar} from '../market/demo-sessions';
import {DEMO_INSTRUMENT_SPECS} from '../market/prices';
import {createDemoSignals} from './demo-signals';
import {canonicalJson, sha256Hex} from './sha256';

/** Validator signature injected by the composition root. */
export type DefinitionValidator = (
  definition: StrategyDefinition
) => ValidationResult;

/** Options of the DEMO strategy client. */
export interface DemoStrategyOptions {
  /** Clock (UTC ms). */
  now?: () => number;
  /**
   * Structural validator; inject `toValidationResult` from
   * `@joseph/strategy-builder`. A minimal check is used otherwise.
   */
  validate?: DefinitionValidator;
  /** Signal fixtures; defaults to `createDemoSignals(now())`. */
  signals?: Signal[];
}

/**
 * Builds an ApiError.
 * @param status HTTP-like status.
 * @param code Error code.
 * @param message Message.
 * @param fieldErrors Field errors.
 * @return Error.
 */
function apiError(
  status: number,
  code: ErrorCode,
  message: string,
  fieldErrors: Array<{field: string; message: string}> = []
): ApiError {
  return new ApiError(status, {
    code,
    message,
    trace_id: 'demo',
    retryable: false,
    field_errors: fieldErrors,
  });
}

/**
 * Minimal validator used when none is injected.
 * @param definition Definition.
 * @return Validation result.
 */
function minimalValidate(definition: StrategyDefinition): ValidationResult {
  const errors =
    definition.schema_version === 2 && typeof definition.when === 'object'
      ? []
      : [
          {
            path: '/schema_version',
            code: 'schema_version',
            message: 'Only rule schema version 2 is supported.',
          },
        ];
  return {
    valid: errors.length === 0,
    errors,
    warnings: [],
    required_plugins: [],
    required_timeframes: [definition.signal_timeframe],
    node_count: 0,
    max_depth: 0,
    definition_hash: null,
  };
}

/** DEMO strategy service. */
export class DemoStrategyClient implements StrategyClient {
  private readonly now: () => number;
  private readonly validator: DefinitionValidator;
  private readonly versions = new Map<string, StrategyVersion[]>();
  private readonly signals: Signal[];
  private readonly signalListeners = new Set<(signal: Signal) => void>();
  private readonly subscriptions = new Map<string, AlertSubscription>();
  private backtests = 0;

  /**
   * Creates the client.
   * @param options Options.
   */
  constructor(options: DemoStrategyOptions = {}) {
    this.now = options.now ?? Date.now;
    this.validator = options.validate ?? minimalValidate;
    this.signals = [...(options.signals ?? createDemoSignals(this.now()))];
  }

  /**
   * Subscribes to emitted signals (used by `DemoUserStream`).
   * @param listener Listener.
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
   * @param signal Signal.
   */
  emitSignal(signal: Signal): void {
    this.signals.unshift(signal);
    for (const listener of [...this.signalListeners]) {
      listener(signal);
    }
  }

  /**
   * Computes the definition hash.
   * @param definition Definition.
   * @return `sha256:<hex>`.
   */
  private hash(definition: StrategyDefinition): string {
    return `sha256:${sha256Hex(canonicalJson(definition))}`;
  }

  /**
   * Lists the latest version of each strategy.
   * @return Versions.
   */
  async listStrategies(): Promise<StrategyVersionList> {
    const latest = [...this.versions.values()]
      .map(list => list[list.length - 1])
      .filter((v): v is StrategyVersion => v !== undefined);
    latest.sort((a, b) => b.created_at_ms - a.created_at_ms);
    return {versions: latest};
  }

  /**
   * Lists all versions of a strategy.
   * @param strategyId Strategy id.
   * @return Versions, newest first.
   */
  async getStrategy(strategyId: string): Promise<StrategyVersionList> {
    const list = this.versions.get(strategyId);
    if (!list) throw apiError(404, 'NOT_FOUND', `No strategy ${strategyId}.`);
    return {versions: [...list].reverse()};
  }

  /**
   * Validates a definition.
   * @param definition Definition.
   * @return Result with hash when valid.
   */
  async validate(definition: StrategyDefinition): Promise<ValidationResult> {
    const result = this.validator(definition);
    return {
      ...result,
      definition_hash: result.valid ? this.hash(definition) : null,
    };
  }

  /**
   * Validates or throws.
   * @param definition Definition.
   * @return Hash.
   */
  private async requireValid(definition: StrategyDefinition): Promise<string> {
    const result = await this.validate(definition);
    if (!result.valid || !result.definition_hash) {
      throw apiError(
        422,
        'VALIDATION_FAILED',
        'The strategy has validation errors.',
        result.errors.map(e => ({field: e.path, message: e.message}))
      );
    }
    return result.definition_hash;
  }

  /**
   * Builds an immutable version record.
   * @param definition Definition.
   * @param version Version number.
   * @param hash Definition hash.
   * @return Version.
   */
  private makeVersion(
    definition: StrategyDefinition,
    version: number,
    hash: string
  ): StrategyVersion {
    return {
      strategy_version_id: `${definition.strategy_id}:v${version}`,
      strategy_id: definition.strategy_id,
      version,
      tenant_id: 'demo-tenant',
      author_user_id: 'demo-user',
      created_at_ms: this.now(),
      definition: JSON.parse(JSON.stringify(definition)) as StrategyDefinition,
      definition_hash: hash,
      compiler_version: 'demo-1.0.0',
      evaluator_version: 'demo-1.0.0',
      state: 'VALIDATED',
      activation: null,
    };
  }

  /**
   * Creates a strategy as version 1.
   * @param definition Definition.
   * @return Version 1.
   */
  async createStrategy(
    definition: StrategyDefinition
  ): Promise<StrategyVersion> {
    const hash = await this.requireValid(definition);
    if (this.versions.has(definition.strategy_id)) {
      throw apiError(
        409,
        'CONFLICT',
        'A strategy with this id already exists. Save a new version instead.'
      );
    }
    const version = this.makeVersion(definition, 1, hash);
    this.versions.set(definition.strategy_id, [version]);
    return version;
  }

  /**
   * Stores a new immutable version.
   * @param strategyId Strategy id.
   * @param definition Definition.
   * @return New version.
   */
  async createVersion(
    strategyId: string,
    definition: StrategyDefinition
  ): Promise<StrategyVersion> {
    const list = this.versions.get(strategyId);
    if (!list) throw apiError(404, 'NOT_FOUND', `No strategy ${strategyId}.`);
    if (definition.strategy_id !== strategyId) {
      throw apiError(
        422,
        'VALIDATION_FAILED',
        'The strategy id cannot change between versions.',
        [{field: '/strategy_id', message: 'Must match the existing strategy.'}]
      );
    }
    const hash = await this.requireValid(definition);
    const version = this.makeVersion(definition, list.length + 1, hash);
    list.push(version);
    return version;
  }

  /**
   * Activates one version; AUTO_LIVE is never accepted here.
   * @param strategyId Strategy id.
   * @param request Version and mode.
   * @return Activated version.
   */
  async activate(
    strategyId: string,
    request: ActivateRequest
  ): Promise<StrategyVersion> {
    if ((request.mode as string) === 'AUTO_LIVE') {
      throw apiError(
        403,
        'TRADE_MODE_DISABLED',
        'Automatic live trading needs an execution deployment with explicit consent.'
      );
    }
    const list = this.versions.get(strategyId);
    const index = list?.findIndex(v => v.version === request.version) ?? -1;
    if (!list || index < 0) {
      throw apiError(404, 'NOT_FOUND', 'Version not found.');
    }
    for (let i = 0; i < list.length; i += 1) {
      const current = list[i];
      if (
        current &&
        i !== index &&
        (current.state === 'ACTIVE' || current.state === 'PAUSED')
      ) {
        list[i] = {...current, state: 'VALIDATED', activation: null};
      }
    }
    const target = list[index];
    if (!target) throw apiError(404, 'NOT_FOUND', 'Version not found.');
    const activated: StrategyVersion = {
      ...target,
      state: 'ACTIVE',
      activation: {
        mode: request.mode,
        activated_at_ms: this.now(),
        instruments: request.instruments ?? [],
      },
    };
    list[index] = activated;
    return activated;
  }

  /**
   * Pauses the active version.
   * @param strategyId Strategy id.
   * @return Paused version.
   */
  async pause(strategyId: string): Promise<StrategyVersion> {
    const list = this.versions.get(strategyId);
    const index = list?.findIndex(v => v.state === 'ACTIVE') ?? -1;
    const active = list?.[index];
    if (!list || !active) {
      throw apiError(409, 'CONFLICT', 'No active version to pause.');
    }
    const paused: StrategyVersion = {...active, state: 'PAUSED'};
    list[index] = paused;
    return paused;
  }

  /**
   * Returns a canned DEMO backtest report.
   * @param strategyId Strategy id.
   * @param request Request.
   * @return Report explicitly marked DEMO.
   */
  async backtest(
    strategyId: string,
    request: BacktestRequest
  ): Promise<BacktestReport> {
    const list = this.versions.get(strategyId);
    const version = list?.find(v => v.version === request.version);
    if (!version) {
      throw apiError(
        404,
        'NOT_FOUND',
        'Save the strategy before running a backtest.'
      );
    }
    if (request.to_ms <= request.from_ms) {
      throw apiError(
        422,
        'VALIDATION_FAILED',
        'The end must be after the start.'
      );
    }
    this.backtests += 1;
    const timeframe = version.definition.signal_timeframe;
    const bar = TIMEFRAME_MS[timeframe];
    // Session instruments (DEMO stocks) align entries and exits to their
    // exchange calendar; perpetuals keep UTC bars.
    const spec = DEMO_INSTRUMENT_SPECS.find(
      s => s.instrumentId === request.instrument_id
    );
    const calendar = demoCalendar(spec?.calendar ?? '24x7');
    const direction = version.definition.direction;
    const span = request.to_ms - request.from_ms;
    const rs = ['2.1', '-1', '1.4', '-1', '2.6'];
    const trades = rs.map((r, i) => {
      const at = request.from_ms + (span * (i + 1)) / 6;
      const entry = calendar.continuous
        ? Math.floor(at / bar) * bar
        : calendar.floor(timeframe, at);
      return {
        signal_id: `demo-bt-${this.backtests}-${i}`,
        direction,
        entry_ms: entry,
        entry_price: '100',
        stop_price: direction === 'LONG' ? '99' : '101',
        target_price: direction === 'LONG' ? '102' : '98',
        exit_ms: calendar.continuous
          ? entry + 6 * bar
          : calendar.shift(timeframe, entry, 6),
        exit_price:
          direction === 'LONG'
            ? String(100 + Number(r))
            : String(100 - Number(r)),
        exit_reason: r.startsWith('-')
          ? ('STOP' as const)
          : ('TARGET' as const),
        r_multiple: r,
        fees_r: '0.05',
        hold_bars: 6,
      };
    });
    return {
      backtest_id: `demo-backtest-${this.backtests}`,
      strategy_version_id: version.strategy_version_id,
      instrument_id: request.instrument_id,
      from_ms: request.from_ms,
      to_ms: request.to_ms,
      bars_evaluated: calendar.continuous
        ? Math.floor(span / bar)
        : calendar.barsBetween(timeframe, request.from_ms, request.to_ms),
      signals: [],
      trades,
      metrics: {
        trades: 5,
        wins: 3,
        losses: 2,
        win_rate: '0.6',
        expectancy_r: '0.82',
        profit_factor: '3.05',
        max_drawdown_r: '1',
        avg_r: '0.82',
        avg_hold_bars: '6',
      },
      missing_bars: 0,
      feed_state: 'DEMO',
      assumptions: [
        'DEMO: synthetic report generated in your browser. It is not a backtest and says nothing about how this strategy performs.',
        'Connect the market engine to replay closed bars point-in-time with fees, slippage and the production evaluator.',
      ],
      evaluator_version: 'demo-1.0.0',
    };
  }

  /**
   * Lists DEMO notification subscriptions. Only the in-app channel is
   * configured, so external channels are stored but never delivered.
   * @return Subscriptions.
   */
  async listAlertSubscriptions(): Promise<AlertSubscriptionList> {
    return {
      subscriptions: [...this.subscriptions.values()],
      configured_channels: ['UI'],
    };
  }

  /**
   * Stores a DEMO notification subscription.
   * @param subscription Subscription.
   * @return Stored subscription.
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
    this.subscriptions.set(stored.subscription_id, stored);
    return stored;
  }

  /**
   * Lists signals, newest first.
   * @param query Filters.
   * @return Signals.
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
