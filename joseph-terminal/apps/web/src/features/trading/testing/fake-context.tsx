/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Self-contained fake `FeatureContext` for the feature
 * component tests (trading, portfolio, alerts, screener, strategy,
 * watchlist), and `renderFeature` to activate a feature and render one of
 * its slot views.
 *
 * Uses only the feature-registry's public types and `createStore`, plus the
 * DEMO broker and strategy services, so tests do not depend on the shell.
 */

import type {OverlayLayer} from '@joseph/chart-core';
import {
  DEMO_BROKER_INSTRUMENTS,
  DEMO_MARKS,
  DemoExecutionClient,
  DemoStrategyClient,
  DemoStudyClient,
  DemoUserStream,
} from '@joseph/mock-provider';
import {
  SessionCalendarCache,
  type BackendClients,
  type MarketDataClient,
  type QualificationReport,
  type StreamClient,
} from '@joseph/contracts';
import {
  createStore,
  type CapabilitySet,
  type CommandSpec,
  type Disposer,
  type EventBus,
  type ReadableStore,
  type SlotContribution,
  type TerminalEventMap,
  type TerminalSelection,
  type UiSlot,
} from '@joseph/feature-registry';
import {render, type RenderResult} from '@testing-library/react';
import type {ReactNode} from 'react';
import type {
  WebFeature,
  WebFeatureContext,
  WebView,
} from '../../../shell/react-bindings';

/** Recorded event emission. */
export interface EmittedEvent {
  /** Event type. */
  type: keyof TerminalEventMap;
  /** Payload. */
  payload: unknown;
}

/** Fake context with inspection helpers. */
export interface FakeContext extends WebFeatureContext {
  /** Writable selection for tests. */
  readonly selectionStore: ReturnType<typeof createStore<TerminalSelection>>;
  /** Events emitted through the bus. */
  readonly emitted: EmittedEvent[];
  /** Notifications shown. */
  readonly notifications: Array<{message: string; tone: string}>;
  /** Registered layer providers. */
  readonly layerProviders: Array<
    (chart: {
      chartId: string;
      instrumentId: string;
      timeframe: TerminalSelection['timeframe'];
    }) => ReadableStore<OverlayLayer | null>
  >;
  /** DEMO broker behind `clients.execution`. */
  readonly broker: DemoExecutionClient;
  /** DEMO strategy client behind `clients.strategy`. */
  readonly strategy: DemoStrategyClient;
  /**
   * Returns contributions of a slot.
   * @param slot Slot.
   * @return Contributions.
   */
  contributions(slot: UiSlot): ReadonlyArray<SlotContribution<WebView>>;
}

/** Static DEMO qualification report used by screener tests. */
export const FAKE_QUALIFICATION: QualificationReport = {
  policy_version: 'demo-policy-1',
  as_of_ms: 1_800_000_000_000,
  venue: null,
  records: [
    {
      instrument_id: 'mock:perp:BTCUSD',
      venue: 'mock',
      display_symbol: 'BTCUSD',
      status: 'QUALIFIED',
      risk_tags: [],
      gates: [
        {
          gate: 'TURNOVER_24H',
          observed: '820000000',
          threshold: '50000000',
          comparator: 'gt',
          passed: true,
          unit: 'USD',
          reason: 'Above threshold',
        },
        {
          gate: 'OPEN_INTEREST_USD',
          observed: '410000000',
          threshold: '10000000',
          comparator: 'gt',
          passed: true,
          unit: 'USD',
          reason: 'Above threshold',
        },
        {
          gate: 'TURNOVER_OI_RATIO',
          observed: '2',
          threshold: '0.2',
          comparator: 'gt',
          passed: true,
          unit: 'ratio',
          reason: 'Above threshold',
        },
        {
          gate: 'SPREAD_PCT',
          observed: '0.001',
          threshold: '0.1',
          comparator: 'lt',
          passed: true,
          unit: 'pct',
          reason: 'Below threshold',
        },
        {
          gate: 'SLIPPAGE_50K_PCT',
          observed: '0.01',
          threshold: '0.25',
          comparator: 'lt',
          passed: true,
          unit: 'pct',
          reason: 'Below threshold',
        },
      ],
      funding: {normalized_8h_pct: '0.01', interval_hours: 8, risk: 'NORMAL'},
      asset_class: 'CRYPTO',
      heat_score: 92,
      rank: 1,
      policy_version: 'demo-policy-1',
      as_of_ms: 1_800_000_000_000,
      reasons: ['All five gates passed; funding normal.'],
    },
    {
      instrument_id: 'mock:perp:SOLUSD',
      venue: 'mock',
      display_symbol: 'SOLUSD',
      status: 'INSUFFICIENT_DATA',
      risk_tags: ['WATCH'],
      gates: [
        {
          gate: 'TURNOVER_24H',
          observed: '61000000',
          threshold: '50000000',
          comparator: 'gt',
          passed: true,
          unit: 'USD',
          reason: 'Above threshold',
        },
        {
          gate: 'OPEN_INTEREST_USD',
          observed: null,
          threshold: '10000000',
          comparator: 'gt',
          passed: null,
          unit: 'USD',
          reason: 'Open interest is missing',
        },
        {
          gate: 'TURNOVER_OI_RATIO',
          observed: null,
          threshold: '0.2',
          comparator: 'gt',
          passed: null,
          unit: 'ratio',
          reason: 'Needs open interest',
        },
        {
          gate: 'SPREAD_PCT',
          observed: '0.12',
          threshold: '0.1',
          comparator: 'lt',
          passed: false,
          unit: 'pct',
          reason: 'Spread too wide',
        },
        {
          gate: 'SLIPPAGE_50K_PCT',
          observed: '0.1',
          threshold: '0.25',
          comparator: 'lt',
          passed: true,
          unit: 'pct',
          reason: 'Below threshold',
        },
      ],
      funding: {normalized_8h_pct: '0.15', interval_hours: 8, risk: 'WATCH'},
      asset_class: 'CRYPTO',
      heat_score: null,
      rank: null,
      policy_version: 'demo-policy-1',
      as_of_ms: 1_800_000_000_000,
      reasons: ['Open interest unavailable; status cannot be qualified.'],
    },
  ],
};

/**
 * Creates a minimal DEMO market client for tests.
 * @return Market data client.
 */
export function fakeMarketClient(): MarketDataClient {
  const unsupported = () => Promise.reject(new Error('not used in this test'));
  return {
    listVenues: unsupported,
    listInstruments: async query => ({
      instruments: DEMO_BROKER_INSTRUMENTS.filter(
        i =>
          (!query.venue || i.key.venue === query.venue) &&
          (!query.q ||
            i.instrument_id.toLowerCase().includes(query.q.toLowerCase()))
      ),
      as_of_ms: 1,
    }),
    getCandles: async query => {
      // Two closed daily bars and today's open bar at the DEMO mark, so
      // securities get a previous-close reference for price limits.
      const mark = DEMO_MARKS[query.instrumentId];
      if (!mark) throw new Error('not used in this test');
      const day = 86_400_000;
      const candles = [-2, -1, 0].map(offset => ({
        instrument_id: query.instrumentId,
        timeframe: query.timeframe,
        start_ms: 1_800_000_000_000 + offset * day,
        end_ms: 1_800_000_000_000 + (offset + 1) * day,
        open: mark,
        high: mark,
        low: mark,
        close: mark,
        base_volume: '1000',
        quote_turnover: null,
        trade_count: null,
        is_final: offset < 0,
        revision: 0,
        source: 'mock',
        feed_state: 'DEMO' as const,
        received_at_ms: 1,
        event_ts_ms: null,
      }));
      return {
        schema_version: 1,
        instrument_id: query.instrumentId,
        timeframe: query.timeframe,
        candles,
        feed_state: 'DEMO',
        source: 'mock',
        as_of_ms: 1,
        complete: true,
        gaps: [],
      };
    },
    getSnapshot: async instrumentId => ({
      instrument_id: instrumentId,
      last: DEMO_MARKS[instrumentId] ?? null,
      mark: DEMO_MARKS[instrumentId] ?? null,
      change_24h_pct: '1.25',
      funding: null,
      open_interest: null,
      feed_state: 'DEMO',
      source: 'mock',
      as_of_ms: Date.now(),
      data_age_ms: 0,
      stale: false,
    }),
    getOrderBook: unsupported,
    getQualification: async (venue, status) => ({
      ...FAKE_QUALIFICATION,
      records: FAKE_QUALIFICATION.records.filter(
        r => (!venue || r.venue === venue) && (!status || r.status === status)
      ),
    }),
  };
}

/**
 * Creates a stream that reports itself open and never emits.
 * @return Stream client.
 */
function silentStream(): StreamClient {
  return {
    subscribe: () => () => undefined,
    onStatus: handler => {
      handler('OPEN');
      return () => undefined;
    },
    close: () => undefined,
  };
}

/** Options of `createFakeContext`. */
export interface FakeContextOptions {
  /** Client overrides. */
  clients?: Partial<BackendClients>;
  /** Initial selection overrides. */
  selection?: Partial<TerminalSelection>;
  /**
   * Acknowledgement and fill delay of the DEMO broker in milliseconds
   * (default 0); ignored when `broker` is given.
   */
  brokerDelayMs?: number;
  /** DEMO broker to use (e.g. a subclass rehearsing LIVE). */
  broker?: DemoExecutionClient;
}

/**
 * Builds a fake feature context backed by DEMO services, on the DEMO BTCUSD
 * perpetual, 5m, paper mode.
 * @param options Client, selection and broker overrides.
 * @return Fake context.
 */
export function createFakeContext(
  options: FakeContextOptions = {}
): FakeContext {
  const delay = options.brokerDelayMs ?? 0;
  const broker =
    options.broker ??
    new DemoExecutionClient({
      ackDelayMs: delay,
      fillDelayMs: delay,
      partialFills: false,
    });
  const strategy = new DemoStrategyClient();
  const market = fakeMarketClient();
  const clients: BackendClients = {
    market,
    marketStream: silentStream(),
    strategy,
    execution: broker,
    userStream: new DemoUserStream(broker, strategy),
    studies: new DemoStudyClient(market),
    demo: true,
    ...options.clients,
  };
  const selectionStore = createStore<TerminalSelection>({
    chartId: 'chart-1',
    instrumentId: 'mock:perp:BTCUSD',
    timeframe: '5m',
    feedMode: 'AUTO',
    accountId: null,
    orderMode: 'PAPER',
    ...options.selection,
  });
  const slots = new Map<
    UiSlot,
    ReturnType<typeof createStore<ReadonlyArray<SlotContribution<WebView>>>>
  >();
  const slotStore = (slot: UiSlot) => {
    let store = slots.get(slot);
    if (!store) {
      store = createStore<ReadonlyArray<SlotContribution<WebView>>>([]);
      slots.set(slot, store);
    }
    return store;
  };
  const commands = new Map<string, CommandSpec>();
  const listeners = new Map<string, Set<(payload: unknown) => void>>();
  const emitted: EmittedEvent[] = [];
  const events: EventBus = {
    emit(type, payload) {
      emitted.push({type, payload});
      for (const handler of [...(listeners.get(type) ?? [])]) {
        handler(payload);
      }
    },
    // Handlers are stored type-erased; `emit` passes each one only the
    // payload of the event type it subscribed to.
    on(type, handler) {
      const set = listeners.get(type) ?? new Set();
      set.add(handler as (payload: unknown) => void);
      listeners.set(type, set);
      return () => {
        set.delete(handler as (payload: unknown) => void);
      };
    },
  };
  const notifications: Array<{message: string; tone: string}> = [];
  const layerProviders: FakeContext['layerProviders'] = [];
  const settings = new Map<string, unknown>();
  const capabilities = createStore<CapabilitySet>({
    names: new Set(['demo', 'market', 'strategy', 'execution']),
  });
  const ctx: FakeContext = {
    featureId: 'test',
    clients,
    calendars: new SessionCalendarCache(
      clients.market.getSessions?.bind(clients.market) ?? null
    ),
    selection: selectionStore,
    selectionStore,
    select(patch) {
      selectionStore.update(current => ({...current, ...patch}));
    },
    capabilities,
    commands: {
      register(command) {
        if (commands.has(command.id)) {
          throw new Error(`Duplicate command ${command.id}`);
        }
        commands.set(command.id, command);
        return () => {
          commands.delete(command.id);
        };
      },
      async execute(id, args) {
        const command = commands.get(id);
        if (!command) throw new Error(`Unknown command ${id}`);
        await command.run(args);
      },
      list: () => [...commands.values()],
    },
    hotkeys: {bind: () => () => undefined},
    slots: {
      contribute(slot, contribution) {
        const store = slotStore(slot);
        store.update(list =>
          [...list, contribution].sort((a, b) => a.order - b.order)
        );
        return () =>
          void store.update(list => list.filter(c => c !== contribution));
      },
      observe: slot => slotStore(slot),
    },
    chartLayers: {
      register(provider) {
        layerProviders.push(provider);
        return () => {
          const index = layerProviders.indexOf(provider);
          if (index >= 0) layerProviders.splice(index, 1);
        };
      },
    },
    events,
    notifier: {
      notify(message, tone = 'info') {
        notifications.push({message, tone});
      },
    },
    // One feature per fake context, so one settings value; like the real
    // store, the caller's type is trusted.
    settings: {
      read<T>(fallback: T): T {
        return (settings.get('value') as T | undefined) ?? fallback;
      },
      write<T>(value: T) {
        settings.set('value', value);
      },
    },
    emitted,
    notifications,
    layerProviders,
    broker,
    strategy,
    contributions: slot => slotStore(slot).get(),
  };
  return ctx;
}

/** Result of `renderFeature`. */
export interface RenderedFeature extends RenderResult {
  /** Context used. */
  ctx: FakeContext;
  /** Feature disposer. */
  dispose: Disposer;
}

/**
 * Activates a feature and renders one of its slot contributions.
 * @param feature Feature to activate.
 * @param slot Slot to render from.
 * @param contributionId Contribution id (first one when omitted).
 * @param ctx Pre-built context; a fresh `createFakeContext()` by default.
 * @return Render result with context and disposer.
 * @throws `Error` when the slot has no matching contribution.
 */
export async function renderFeature(
  feature: WebFeature,
  slot: UiSlot,
  contributionId?: string,
  ctx: FakeContext = createFakeContext()
): Promise<RenderedFeature> {
  const dispose = await feature.activate(ctx);
  const contribution = ctx
    .contributions(slot)
    .find(c => !contributionId || c.id === contributionId);
  if (!contribution) throw new Error(`No contribution in ${slot}`);
  const View = contribution.view;
  const element: ReactNode = <View ctx={ctx} />;
  return {...render(element), ctx, dispose};
}
