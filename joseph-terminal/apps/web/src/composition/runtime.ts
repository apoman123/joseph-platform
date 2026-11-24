/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Terminal runtime: the composition root's object graph.
 *
 * Creates registries, stores and clients once per browser session and wires
 * them into the feature registry. Shell features receive this runtime;
 * third-party features only ever see their scoped `FeatureContext`.
 */

import {
  SessionCalendarCache,
  type BackendClients,
  type FeedState,
  type InstrumentId,
  type OrderMode,
  type WorkspaceDocument,
} from '@joseph/contracts';
import type {ChartPort} from '@joseph/chart-core';
import {
  ToolRegistry,
  type DrawingController,
  type DrawingSeriesSync,
  type DrawingStore,
} from '@joseph/drawing-core';
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
  EMPTY_DETACH_STATE,
  TemplateRepository,
  WorkspaceRepository,
  activeChart,
  createAutosaver,
  createDefaultWorkspace,
  workspaceReducer,
  type DetachState,
  type DetachedPaneRef,
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
  /** Id, increasing per runtime; used to dismiss the toast. */
  id: number;
  /** Text shown to the user. */
  message: string;
  /** Tone; sets the accent color. */
  tone: 'info' | 'success' | 'warning' | 'error';
}

/** Point shared between charts for crosshair sync. */
export interface SyncedPoint {
  /** Chart the pointer is over; it ignores its own broadcast. */
  sourceChartId: string;
  /** Time (UTC ms) and price, or null when the pointer left the chart. */
  point: {timeMs: number; price: number} | null;
}

/** In-memory publish/subscribe channel without a current value. */
export interface Broadcast<T> {
  /**
   * Delivers a value synchronously to every current subscriber.
   * @param value Value to deliver.
   */
  emit(value: T): void;
  /**
   * Subscribes a handler to later values.
   * @param handler Called with each emitted value.
   * @return Function that unsubscribes the handler.
   */
  subscribe(handler: (value: T) => void): Disposer;
}

/**
 * Creates a broadcast channel.
 * @return Channel with no subscribers.
 */
export function createBroadcast<T>(): Broadcast<T> {
  const handlers = new Set<(value: T) => void>();
  return {
    emit(value) {
      // Iterate a copy: a handler may unsubscribe (or subscribe) while the
      // value is being delivered.
      for (const handler of [...handlers]) {
        handler(value);
      }
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
  /** Engine series of engine-backed drawings, per chart id. */
  series: Map<string, DrawingSeriesSync>;
  /** Armed tool id (null = pointer). */
  activeTool: WritableStore<string | null>;
  /** Whether magnet snapping to bar prices is on (saved per browser). */
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
  /** Indicator plugin id, e.g. `order_flow_pressure`. */
  pluginId: string;
}

/** Window actions for detached panes (main window only). */
export interface WindowActions {
  /** Whether this browser can link windows (BroadcastChannel). */
  readonly supported: boolean;
  /**
   * Opens a pane in another window. Call from a click handler: browsers
   * block windows opened without a user gesture.
   * @param ref Pane to detach.
   * @param title Title shown in placeholders and messages.
   */
  detach(ref: DetachedPaneRef, title: string): void;
  /**
   * Closes the pane's window and shows the pane here again.
   * @param key Pane key (`paneKey` of its reference).
   */
  redock(key: string): void;
  /**
   * Brings the pane's window to the front.
   * @param key Pane key (`paneKey` of its reference).
   */
  focus(key: string): void;
  /**
   * Re-opens a remembered pane's window (from a click).
   * @param key Pane key (`paneKey` of its reference).
   */
  reopen(key: string): void;
  /** Re-opens every remembered pane without a window (from a click). */
  reopenAll(): void;
}

/** Window role and pop-out wiring. */
export interface RuntimeOptions {
  /** `main` owns and saves the workspace; `popout` mirrors it. */
  role?: 'main' | 'popout';
  /** Workspace id (default `default`). */
  workspaceId?: string;
  /**
   * Chart a pop-out shows; it stays the active chart, so commands, the
   * order ticket and trading guards (replay) refer to it.
   */
  pinnedChartId?: string | null;
  /**
   * Receives every workspace action applied locally (pop-outs forward
   * them to the main window).
   * @param action Applied action.
   */
  onAction?: (action: WorkspaceAction) => void;
}

/** Everything the shell owns. */
export interface TerminalRuntime {
  /** Window role. */
  role: 'main' | 'popout';
  /** Build-time configuration. */
  config: TerminalConfig;
  /** Backend clients. */
  clients: BackendClients;
  /**
   * Exchange session calendars (engine `GET /v1/calendars/…/sessions`, or
   * generated in DEMO), shared by charts, replay, drawings and features.
   */
  calendars: SessionCalendarCache;
  /** Capabilities the clients provide; features require them by name. */
  capabilities: WritableStore<CapabilitySet>;
  /** Browser storage (safe when blocked). */
  storage: KeyValueStorage;
  /** Workspace document. */
  workspace: ReadableStore<WorkspaceDocument>;
  /**
   * Applies a workspace action (a pop-out also forwards it to the main
   * window).
   * @param action Action to apply.
   */
  dispatch(action: WorkspaceAction): void;
  /** Selected trading account id, or null before one is chosen. */
  account: WritableStore<string | null>;
  /** Selected order mode. LIVE is never the default. */
  orderMode: WritableStore<OrderMode>;
  /**
   * Selection read model derived from the active chart, account, order mode
   * and replay state; it changes only when one of its fields does.
   */
  selection: ReadableStore<TerminalSelection>;
  /** Command registry (palette, hotkeys, menus). */
  commands: CommandRegistry;
  /** Hotkey bindings to commands. */
  hotkeys: HotkeyHost;
  /** UI slots that features contribute views to. */
  slots: SlotRegistry<WebView>;
  /** Chart overlay layer providers. */
  chartLayers: ChartLayerHost;
  /** Event bus between features. */
  events: EventBus;
  /** Shows toasts (at most four at a time, each for 6 s). */
  notifier: Notifier;
  /** Feature registry. */
  registry: FeatureRegistry<WebView>;
  /** Registered drawing tools. */
  tools: ToolRegistry;
  /** Drawing state shared by panes and the toolbar. */
  drawing: DrawingSession;
  /** Toasts on screen, oldest first. */
  toasts: WritableStore<readonly Toast[]>;
  /** Color theme (saved per browser). */
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
  /** Whether the data window is shown on charts (saved per browser). */
  dataWindow: WritableStore<boolean>;
  /** Open indicator settings dialog, if any. */
  indicatorSettings: WritableStore<IndicatorSettingsRequest | null>;
  /** Saved chart templates. */
  templates: TemplateRepository;
  /** Panes living in other windows (main window view). */
  detached: WritableStore<DetachState>;
  /** Pop-out window actions; null until linked (and in pop-outs). */
  windows: WritableStore<WindowActions | null>;
  /**
   * Replaces the workspace with one from another window without
   * forwarding it (pop-outs adopt the main window's document).
   * @param doc Workspace document.
   */
  adoptWorkspace(doc: WorkspaceDocument): void;
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
 * @return Local storage in the browser, memory storage during server
 *     rendering.
 */
function createStorage(): KeyValueStorage {
  if (typeof window === 'undefined') return new MemoryStorage();
  return new BrowserStorage(() => window.localStorage);
}

/**
 * Reads the saved theme.
 * @param storage Browser storage.
 * @return Saved theme; dark unless light was saved.
 */
function savedTheme(storage: KeyValueStorage): 'dark' | 'light' {
  return storage.getItem('joseph.theme') === 'light' ? 'light' : 'dark';
}

/**
 * Creates the runtime and registers features.
 * @param features Returns the features to register, given the runtime.
 * @param config Configuration; defaults to the build-time one (tests pass
 *     their own).
 * @param options Window role (main or pop-out) and pop-out wiring.
 * @return Runtime whose features are registered but not yet activated.
 */
export function createRuntime(
  features: (runtime: TerminalRuntime) => readonly WebFeature[],
  config: TerminalConfig = readConfig(),
  options: RuntimeOptions = {}
): TerminalRuntime {
  const role = options.role ?? 'main';
  const workspaceId = options.workspaceId ?? 'default';
  const pinned = options.pinnedChartId ?? null;
  /**
   * Keeps a pop-out's chart active.
   * @param doc Workspace.
   * @return Workspace with the pinned chart active.
   */
  const pin = (doc: WorkspaceDocument): WorkspaceDocument =>
    pinned !== null &&
    doc.active_chart_id !== pinned &&
    doc.charts.some(chart => chart.chart_id === pinned)
      ? {...doc, active_chart_id: pinned}
      : doc;
  const storage = createStorage();
  const bundle = buildClients(config);
  const market = bundle.clients.market;
  const calendars = new SessionCalendarCache(
    market.getSessions?.bind(market) ?? null
  );
  const repository = new WorkspaceRepository(storage);
  // Workspace writes are debounced by 400 ms.
  const autosaver = createAutosaver(repository, 400);
  const defaultInstrument: InstrumentId = config.demo
    ? 'mock:perp:BTCUSD'
    : 'binance:perp:BTCUSDT';
  const now = Date.now();
  const workspace = createStore<WorkspaceDocument>(
    pin(
      repository.load(workspaceId, now) ??
        createDefaultWorkspace({
          workspaceId,
          instrumentId: defaultInstrument,
          timeframe: '5m',
          nowMs: now,
        })
    )
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
    // `Object.keys` widens to `string[]`; `next` was built with exactly the
    // `TerminalSelection` keys.
    const changed = (Object.keys(next) as Array<keyof TerminalSelection>).some(
      key => next[key] !== current[key]
    );
    if (changed) selection.set(next);
  };
  workspace.subscribe(refreshSelection);
  replay.subscribe(refreshSelection);
  account.subscribe(refreshSelection);
  orderMode.subscribe(refreshSelection);
  // Only the main window writes the workspace; pop-outs mirror it.
  if (role === 'main') workspace.subscribe(doc => void autosaver.schedule(doc));

  const dispatch = (action: WorkspaceAction) => {
    const before = workspace.get();
    const next = workspaceReducer(before, action, Date.now());
    workspace.set(pin(next));
    if (next !== before) options.onAction?.(action);
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
      // Keep at most four toasts; each disappears after 6 s.
      toasts.update(list => [...list.slice(-3), {id, message, tone}]);
      setTimeout(
        () => void toasts.update(list => list.filter(t => t.id !== id)),
        6000
      );
    },
  };
  const capabilities = createStore<CapabilitySet>({
    names: new Set(bundle.capabilities),
  });
  const theme = createStore<'dark' | 'light'>(savedTheme(storage));
  theme.subscribe(value => void storage.setItem('joseph.theme', value));

  const runtime: TerminalRuntime = {
    role,
    config,
    clients: bundle.clients,
    calendars,
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
    // Assigned right below, before `features(runtime)` can read it.
    registry: undefined as unknown as FeatureRegistry<WebView>,
    panelRequest: createStore<PanelRequest | null>(null),
    tools: new ToolRegistry(),
    drawing: {
      controllers: new Map(),
      stores: new Map(),
      series: new Map(),
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
    detached: createStore<DetachState>(EMPTY_DETACH_STATE),
    windows: createStore<WindowActions | null>(null),
    adoptWorkspace: doc => void workspace.set(pin(doc)),
    flush: () => {
      if (role === 'main') autosaver.flush();
    },
  };
  runtime.dataWindow.subscribe(
    on => void storage.setItem('joseph.dataWindow', on ? 'on' : 'off')
  );
  runtime.drawing.magnet.subscribe(
    on => void storage.setItem('joseph.magnet', on ? 'on' : 'off')
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
      calendars,
    },
    {contractVersion: CONTRACT_VERSION}
  );
  runtime.registry.registerAll(features(runtime));
  return runtime;
}
