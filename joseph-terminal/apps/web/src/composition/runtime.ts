/**
 * @fileoverview Terminal runtime: the composition root's object graph.
 *
 * Creates registries, stores and clients once per browser session and wires
 * them into the feature registry. Shell features receive this runtime;
 * third-party features only ever see their scoped `FeatureContext`.
 */
import type {
  BackendClients,
  FeedState,
  InstrumentId,
  OrderMode,
  WorkspaceDocument,
} from '@joseph/contracts';
import type {ChartPort} from '@joseph/chart-core';
import type {DrawingController, DrawingStore} from '@joseph/drawing-core';
import {ToolRegistry} from '@joseph/drawing-core';
import {
  BrowserStorage,
  FeatureRegistry,
  MemoryStorage,
  createChartLayerRegistry,
  createCommandRegistry,
  createEventBus,
  createHotkeyRegistry,
  createSlotRegistry,
  createStore,
  type CapabilitySet,
  type ChartLayerHost,
  type CommandRegistry,
  type Disposer,
  type EventBus,
  type HotkeyHost,
  type KeyValueStorage,
  type Notifier,
  type ReadableStore,
  type SlotRegistry,
  type TerminalSelection,
  type WritableStore,
} from '@joseph/feature-registry';
import {
  TemplateRepository,
  WorkspaceRepository,
  activeChart,
  createAutosaver,
  createDefaultWorkspace,
  workspaceReducer,
  type PaletteState,
  type ReplayState,
  type WorkspaceAction,
} from '@joseph/terminal-shell';
import type {WebFeature, WebView} from '../shell/react-bindings';
import {buildClients} from './clients';
import {readConfig, type TerminalConfig} from './env';

/** Contract major version shipped by this build. */
export const CONTRACT_VERSION = 1;

/** Toast message. */
export interface Toast {
  /** Unique id. */
  id: number;
  /** Text. */
  message: string;
  /** Tone. */
  tone: 'info' | 'success' | 'warning' | 'error';
}

/** Point shared between charts for crosshair sync. */
export interface SyncedPoint {
  /** Source chart. */
  sourceChartId: string;
  /** Time and price, or null when the pointer left the chart. */
  point: {timeMs: number; price: number} | null;
}

/** Simple broadcast channel. */
export interface Broadcast<T> {
  /**
   * Publishes a value.
   * @param value Value.
   */
  emit(value: T): void;
  /**
   * Subscribes.
   * @param handler Handler.
   * @return Disposer.
   */
  subscribe(handler: (value: T) => void): Disposer;
}

/**
 * Creates a broadcast channel.
 * @return Channel.
 */
export function createBroadcast<T>(): Broadcast<T> {
  const handlers = new Set<(value: T) => void>();
  return {
    emit(value) {
      for (const handler of [...handlers]) handler(value);
    },
    subscribe(handler) {
      handlers.add(handler);
      return () => {
        handlers.delete(handler);
      };
    },
  };
}

/** Drawing state shared by panes, the toolbar and drawing commands. */
export interface DrawingSession {
  /** Controllers per chart id. */
  controllers: Map<string, DrawingController>;
  /** Stores per chart id. */
  stores: Map<string, DrawingStore>;
  /** Armed tool id (null = pointer). */
  activeTool: WritableStore<string | null>;
  /** Magnet snapping. */
  magnet: WritableStore<boolean>;
  /** Bumped whenever history availability may have changed. */
  revision: WritableStore<number>;
  /** Selected drawing (style editor and object list). */
  selection: WritableStore<DrawingSelection | null>;
}

/** A selected drawing on one chart. */
export interface DrawingSelection {
  /** Chart id. */
  chartId: string;
  /** Drawing id. */
  drawingId: string;
}

/** Request to open the indicator settings dialog. */
export interface IndicatorSettingsRequest {
  /** Chart whose layer is configured. */
  chartId: string;
  /** Plugin id. */
  pluginId: string;
}

/** Everything the shell owns. */
export interface TerminalRuntime {
  /** Configuration. */
  config: TerminalConfig;
  /** Backend clients. */
  clients: BackendClients;
  /** Capabilities. */
  capabilities: WritableStore<CapabilitySet>;
  /** Browser storage (safe when blocked). */
  storage: KeyValueStorage;
  /** Workspace document. */
  workspace: ReadableStore<WorkspaceDocument>;
  /**
   * Applies a workspace action.
   * @param action Action.
   */
  dispatch(action: WorkspaceAction): void;
  /** Selected trading account. */
  account: WritableStore<string | null>;
  /** Selected order mode. LIVE is never the default. */
  orderMode: WritableStore<OrderMode>;
  /** Derived selection read model. */
  selection: ReadableStore<TerminalSelection>;
  /** Command registry. */
  commands: CommandRegistry;
  /** Hotkeys. */
  hotkeys: HotkeyHost;
  /** UI slots. */
  slots: SlotRegistry<WebView>;
  /** Chart overlay layers. */
  chartLayers: ChartLayerHost;
  /** Event bus. */
  events: EventBus;
  /** Notifier. */
  notifier: Notifier;
  /** Feature registry. */
  registry: FeatureRegistry<WebView>;
  /** Drawing tools. */
  tools: ToolRegistry;
  /** Drawing session. */
  drawing: DrawingSession;
  /** Toasts. */
  toasts: WritableStore<readonly Toast[]>;
  /** Theme. */
  theme: WritableStore<'dark' | 'light'>;
  /** Command palette state. */
  palette: WritableStore<PaletteState>;
  /** Crosshair sync channel. */
  crosshair: Broadcast<SyncedPoint>;
  /** Provenance reported by each chart pane. */
  feedStates: WritableStore<Readonly<Record<string, FeedState>>>;
  /**
   * Latest request to reveal a panel tab (`shell.showPanel`); slot hosts
   * select and expand the tab when its slot matches.
   */
  panelRequest: WritableStore<PanelRequest | null>;
  /** Bar replay state per chart id (absent = live). */
  replay: WritableStore<Readonly<Record<string, ReplayState>>>;
  /** Mounted chart ports per chart id (screenshots, data window). */
  charts: Map<string, ChartPort>;
  /** Whether the data window is shown on charts. */
  dataWindow: WritableStore<boolean>;
  /** Open indicator settings dialog, if any. */
  indicatorSettings: WritableStore<IndicatorSettingsRequest | null>;
  /** Saved chart templates. */
  templates: TemplateRepository;
  /** Persists pending workspace changes immediately. */
  flush(): void;
}

/** Request to reveal one tab of a tabbed slot. */
export interface PanelRequest {
  /** Slot hosting the tab. */
  slot: 'side-panel' | 'bottom-panel';
  /** Contribution id of the tab. */
  id: string;
  /** Increments per request so repeated requests re-trigger. */
  nonce: number;
}

/**
 * Creates storage that works even when browser storage is blocked.
 * @return Storage.
 */
function createStorage(): KeyValueStorage {
  if (typeof window === 'undefined') return new MemoryStorage();
  return new BrowserStorage(() => window.localStorage);
}

/**
 * Reads the saved theme.
 * @param storage Storage.
 * @return Theme.
 */
function savedTheme(storage: KeyValueStorage): 'dark' | 'light' {
  return storage.getItem('joseph.theme') === 'light' ? 'light' : 'dark';
}

/**
 * Creates the runtime and registers features (not yet activated).
 * @param features Features to compose.
 * @param config Optional configuration override (tests).
 * @return Runtime.
 */
export function createRuntime(
  features: (runtime: TerminalRuntime) => readonly WebFeature[],
  config: TerminalConfig = readConfig()
): TerminalRuntime {
  const storage = createStorage();
  const bundle = buildClients(config);
  const repository = new WorkspaceRepository(storage);
  const autosaver = createAutosaver(repository, 400);
  const defaultInstrument: InstrumentId = config.demo
    ? 'mock:perp:BTCUSD'
    : 'binance:perp:BTCUSDT';
  const now = Date.now();
  const workspace = createStore<WorkspaceDocument>(
    repository.load('default', now) ??
      createDefaultWorkspace({
        workspaceId: 'default',
        instrumentId: defaultInstrument,
        timeframe: '5m',
        nowMs: now,
      })
  );
  const account = createStore<string | null>(null);
  const orderMode = createStore<OrderMode>('PAPER');
  const replay = createStore<Readonly<Record<string, ReplayState>>>({});
  const deriveSelection = (): TerminalSelection => {
    const doc = workspace.get();
    const chart = activeChart(doc);
    return {
      chartId: chart.chart_id,
      instrumentId: chart.instrument_id,
      timeframe: chart.timeframe,
      feedMode: chart.feed_mode,
      accountId: account.get(),
      orderMode: orderMode.get(),
      replay: replay.get()[chart.chart_id] !== undefined,
    };
  };
  const selection = createStore<TerminalSelection>(deriveSelection());
  const refreshSelection = () => {
    const next = deriveSelection();
    const current = selection.get();
    const changed = (Object.keys(next) as (keyof TerminalSelection)[]).some(
      key => next[key] !== current[key]
    );
    if (changed) selection.set(next);
  };
  workspace.subscribe(refreshSelection);
  replay.subscribe(refreshSelection);
  account.subscribe(refreshSelection);
  orderMode.subscribe(refreshSelection);
  workspace.subscribe(doc => autosaver.schedule(doc));

  const dispatch = (action: WorkspaceAction) => {
    workspace.set(workspaceReducer(workspace.get(), action, Date.now()));
  };
  const commands = createCommandRegistry();
  const hotkeys = createHotkeyRegistry(commands);
  const slots = createSlotRegistry<WebView>();
  const chartLayers = createChartLayerRegistry();
  const events = createEventBus();
  const toasts = createStore<readonly Toast[]>([]);
  let toastSeq = 0;
  const notifier: Notifier = {
    notify(message, tone = 'info') {
      toastSeq += 1;
      const id = toastSeq;
      toasts.update(list => [...list.slice(-3), {id, message, tone}]);
      setTimeout(
        () => toasts.update(list => list.filter(t => t.id !== id)),
        6000
      );
    },
  };
  const capabilities = createStore<CapabilitySet>({
    names: new Set(bundle.capabilities),
  });
  const theme = createStore<'dark' | 'light'>(savedTheme(storage));
  theme.subscribe(value => storage.setItem('joseph.theme', value));

  const runtime: TerminalRuntime = {
    config,
    clients: bundle.clients,
    capabilities,
    storage,
    workspace,
    dispatch,
    account,
    orderMode,
    selection,
    commands,
    hotkeys,
    slots,
    chartLayers,
    events,
    notifier,
    registry: undefined as unknown as FeatureRegistry<WebView>,
    panelRequest: createStore<PanelRequest | null>(null),
    tools: new ToolRegistry(),
    drawing: {
      controllers: new Map(),
      stores: new Map(),
      activeTool: createStore<string | null>(null),
      magnet: createStore(storage.getItem('joseph.magnet') === 'on'),
      revision: createStore(0),
      selection: createStore<DrawingSelection | null>(null),
    },
    toasts,
    theme,
    palette: createStore<PaletteState>({open: false, query: '', index: 0}),
    crosshair: createBroadcast<SyncedPoint>(),
    feedStates: createStore<Readonly<Record<string, FeedState>>>({}),
    replay,
    charts: new Map(),
    dataWindow: createStore(storage.getItem('joseph.dataWindow') === 'on'),
    indicatorSettings: createStore<IndicatorSettingsRequest | null>(null),
    templates: new TemplateRepository(storage),
    flush: () => autosaver.flush(),
  };
  runtime.dataWindow.subscribe(on =>
    storage.setItem('joseph.dataWindow', on ? 'on' : 'off')
  );
  runtime.drawing.magnet.subscribe(on =>
    storage.setItem('joseph.magnet', on ? 'on' : 'off')
  );
  runtime.registry = new FeatureRegistry<WebView>(
    {
      clients: bundle.clients,
      selection,
      select: patch => {
        const chartId = activeChart(workspace.get()).chart_id;
        if (patch.instrumentId) {
          dispatch({
            type: 'setInstrument',
            chartId,
            instrumentId: patch.instrumentId,
          });
        }
        if (patch.timeframe) {
          dispatch({type: 'setTimeframe', chartId, timeframe: patch.timeframe});
        }
        if (patch.accountId !== undefined) account.set(patch.accountId);
      },
      capabilities,
      commands,
      hotkeys,
      slots,
      chartLayers,
      events,
      notifier,
      storage,
    },
    {contractVersion: CONTRACT_VERSION}
  );
  runtime.registry.registerAll(features(runtime));
  return runtime;
}
