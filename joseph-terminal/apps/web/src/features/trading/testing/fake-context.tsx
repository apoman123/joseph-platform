/**
 * @fileoverview Self-contained fake `FeatureContext` for F6 component tests.
 *
 * Uses only the frozen feature-registry types and `createStore`, plus the
 * DEMO broker/strategy services, so tests do not depend on the shell.
 */
import type {OverlayLayer} from '@joseph/chart-core';
import {
  DEMO_BROKER_INSTRUMENTS,
  DEMO_MARKS,
  DemoExecutionClient,
  DemoStrategyClient,
  DemoUserStream,
} from '@joseph/mock-provider';
import type {
  BackendClients,
  MarketDataClient,
  QualificationReport,
  StreamClient,
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
import {toValidationResult} from '@joseph/strategy-builder';
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
  readonly notifications: {message: string; tone: string}[];
  /** Registered layer providers. */
  readonly layerProviders: ((chart: {
    chartId: string;
    instrumentId: string;
    timeframe: TerminalSelection['timeframe'];
  }) => ReadableStore<OverlayLayer | null>)[];
  /** DEMO broker behind `clients.execution`. */
  readonly broker: DemoExecutionClient;
  /** DEMO strategy client behind `clients.strategy`. */
  readonly strategy: DemoStrategyClient;
  /**
   * Returns contributions of a slot.
   * @param slot Slot.
   * @return Contributions.
   */
  contributions(slot: UiSlot): readonly SlotContribution<WebView>[];
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
    getCandles: unsupported,
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
    listIndicatorPlugins: unsupported,
    getIndicatorSnapshots: unsupported,
    getAois: unsupported,
    getIndicatorSeries: unsupported,
  };
}

/**
 * Creates a stream that never emits.
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
  /** DEMO broker delays (default 0 ms). */
  brokerDelayMs?: number;
}

/**
 * Builds a fake feature context backed by DEMO services.
 * @param options Options.
 * @return Fake context.
 */
export function createFakeContext(
  options: FakeContextOptions = {}
): FakeContext {
  const delay = options.brokerDelayMs ?? 0;
  const broker = new DemoExecutionClient({
    ackDelayMs: delay,
    fillDelayMs: delay,
    partialFills: false,
  });
  const strategy = new DemoStrategyClient({
    validate: def => toValidationResult(def),
  });
  const clients: BackendClients = {
    market: fakeMarketClient(),
    marketStream: silentStream(),
    strategy,
    execution: broker,
    userStream: new DemoUserStream(broker, strategy),
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
    ReturnType<typeof createStore<readonly SlotContribution<WebView>[]>>
  >();
  const slotStore = (slot: UiSlot) => {
    let store = slots.get(slot);
    if (!store) {
      store = createStore<readonly SlotContribution<WebView>[]>([]);
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
      for (const handler of [...(listeners.get(type) ?? [])]) handler(payload);
    },
    on(type, handler) {
      const set = listeners.get(type) ?? new Set();
      set.add(handler as (payload: unknown) => void);
      listeners.set(type, set);
      return () => {
        set.delete(handler as (payload: unknown) => void);
      };
    },
  };
  const notifications: {message: string; tone: string}[] = [];
  const layerProviders: FakeContext['layerProviders'] = [];
  const settings = new Map<string, unknown>();
  const capabilities = createStore<CapabilitySet>({
    names: new Set(['demo', 'market', 'strategy', 'execution']),
  });
  const ctx: FakeContext = {
    featureId: 'test',
    clients,
    selection: selectionStore,
    selectionStore,
    select(patch) {
      selectionStore.update(current => ({...current, ...patch}));
    },
    capabilities,
    commands: {
      register(command) {
        if (commands.has(command.id))
          throw new Error(`Duplicate command ${command.id}`);
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
        return () => store.update(list => list.filter(c => c !== contribution));
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
 * @param ctx Optional pre-built context.
 * @return Render result with context and disposer.
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
