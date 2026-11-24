/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Feature extension API (V5 §3.4 / §10).
 *
 * Features are vetted modules composed at build time. Each declares a
 * manifest, activates against a `FeatureContext` and returns a disposer that
 * removes every subscription, command, hotkey, slot contribution and overlay
 * layer it registered. Feature flags control exposure only; they can never
 * grant trading permission or bypass server-side risk.
 */

import type {OverlayLayer} from '@joseph/chart-core';
import type {
  BackendClients,
  FeedMode,
  InstrumentId,
  OrderMode,
  SessionCalendarCache,
  Timeframe,
} from '@joseph/contracts';
import type {Disposer, ReadableStore, WritableStore} from './store';

/** UI extension slots provided by the terminal shell. */
export type UiSlot =
  | 'toolbar'
  | 'drawing-toolbar'
  | 'side-panel'
  | 'bottom-panel'
  | 'chart-overlay'
  | 'chart-context-menu'
  | 'status-bar'
  | 'workspace';

/** Feature lifecycle (V5 §10.1). */
export type FeatureLifecycle =
  'UNAVAILABLE' | 'DISABLED' | 'ENABLED' | 'DEPRECATED' | 'REMOVED';

/** Coarse permissions a feature declares; enforced again by the server. */
export type FeaturePermission =
  | 'market:read'
  | 'strategy:read'
  | 'strategy:write'
  | 'orders:read'
  | 'orders:intent'
  | 'risk:kill-switch'
  | 'audit:read';

/** Removal / deprecation plan carried by every manifest. */
export interface RemovalPlan {
  /** Version that deprecated the feature, if any. */
  deprecatedSince?: string;
  /** Replacement feature id, if any. */
  replacement?: string;
  /** How saved data is preserved when the feature is removed. */
  notes: string;
}

/** Static description of a feature. */
export interface FeatureManifest {
  /** Unique id, e.g. `drawing.fib-retracement`. */
  id: string;
  /** Semantic version. */
  version: string;
  /** Owning team or module. */
  owner: string;
  /** Human-readable name. */
  displayName: string;
  /** One-sentence purpose. */
  description: string;
  /** Slots the feature contributes to. */
  uiSlots: readonly UiSlot[];
  /** Capabilities required to enable it (see `CapabilitySet`). */
  requiredCapabilities?: readonly string[];
  /** Feature ids that must activate first. */
  dependencies?: readonly string[];
  /** Permissions requested. */
  permissions?: readonly FeaturePermission[];
  /** Version of the feature's persisted settings schema. */
  settingsSchemaVersion: number;
  /** Major version of `@joseph/contracts` the feature targets. */
  contractVersion: number;
  /** Declared lifecycle; defaults to ENABLED. */
  lifecycle?: FeatureLifecycle;
  /** Removal plan (mandatory for safe deletion). */
  removal: RemovalPlan;
}

/** A contribution of UI to a slot. `TView` is the host's component type. */
export interface SlotContribution<TView> {
  /** Unique id within the slot. */
  id: string;
  /** Tab or button title. */
  title: string;
  /** Sort order (ascending). */
  order: number;
  /** Optional icon name understood by the host. */
  icon?: string;
  /** Optional keyboard shortcut hint shown in tooltips. */
  shortcut?: string;
  /** Host component rendering the contribution. */
  view: TView;
  /**
   * Contributing feature id. Set by the registry (additive field); hosts use
   * it to hand the view its owning feature's scoped context.
   */
  featureId?: string;
}

/** A command that features and hotkeys can invoke. */
export interface CommandSpec {
  /** Unique id, e.g. `ticket.open`. */
  id: string;
  /** Human-readable title for menus and the command palette. */
  title: string;
  /**
   * Executes the command.
   * @param args Command-specific argument.
   * @return Optional promise for async commands.
   */
  run(args?: unknown): void | Promise<void>;
}

/** Command registry. */
export interface CommandRegistry {
  /**
   * Registers a command.
   * @param command Command specification.
   * @return Disposer.
   * @throws {Error} If the id is already registered.
   */
  register(command: CommandSpec): Disposer;
  /**
   * Executes a command by id.
   * @param id Command id.
   * @param args Optional argument.
   * @return Promise resolving when the command finishes.
   * @throws {Error} If the command does not exist.
   */
  execute(id: string, args?: unknown): Promise<void>;
  /**
   * Lists registered commands.
   * @return Command specs.
   */
  list(): readonly CommandSpec[];
}

/** Hotkey registry; combos look like `alt+t`, `mod+z`, `shift+mod+z`. */
export interface HotkeyRegistry {
  /**
   * Binds a key combo to a command.
   * @param combo Normalized combo.
   * @param commandId Command to run.
   * @return Disposer.
   */
  bind(combo: string, commandId: string): Disposer;
}

/** Slot registry. */
export interface SlotRegistry<TView> {
  /**
   * Contributes UI to a slot.
   * @param slot Slot name.
   * @param contribution Contribution.
   * @return Disposer.
   */
  contribute(slot: UiSlot, contribution: SlotContribution<TView>): Disposer;
  /**
   * Observes a slot's contributions, sorted by `order`.
   * @param slot Slot name.
   * @return Store of contributions.
   */
  observe(slot: UiSlot): ReadableStore<ReadonlyArray<SlotContribution<TView>>>;
}

/** In-app events exchanged between features. */
export interface TerminalEventMap {
  /** Secondary click on a chart price (opens chart-trading menus). */
  'chart.contextMenu': {
    chartId: string;
    instrumentId: InstrumentId;
    price: number;
    timeMs: number | null;
  };
  /** Draggable overlay released on a chart. */
  'chart.overlayDragEnd': {
    chartId: string;
    layerId: string;
    modelId: string;
    price: number;
  };
  /** Request to focus an exact signal/AOI on the chart. */
  'signal.focus': {
    instrumentId: InstrumentId;
    timeframe: Timeframe;
    timeMs: number;
    aoiIds: readonly string[];
  };
  /** Request to open the order ticket prefilled. */
  'ticket.open': {
    instrumentId: InstrumentId;
    side?: 'buy' | 'sell';
    orderType?: 'market' | 'limit' | 'stop_market';
    price?: string;
  };
}

/** Typed event bus. */
export interface EventBus {
  /**
   * Publishes an event.
   * @param type Event type.
   * @param payload Event payload.
   */
  emit<K extends keyof TerminalEventMap>(
    type: K,
    payload: TerminalEventMap[K]
  ): void;
  /**
   * Subscribes to an event.
   * @param type Event type.
   * @param handler Handler.
   * @return Disposer.
   */
  on<K extends keyof TerminalEventMap>(
    type: K,
    handler: (payload: TerminalEventMap[K]) => void
  ): Disposer;
}

/** Current user selection shared across features (read model). */
export interface TerminalSelection {
  /** Active chart id. */
  chartId: string;
  /** Instrument on the active chart. */
  instrumentId: InstrumentId;
  /** Timeframe on the active chart. */
  timeframe: Timeframe;
  /** Feed policy of the active chart. */
  feedMode: FeedMode;
  /** Selected trading account, if any. */
  accountId: string | null;
  /** Order mode selected in the UI (LIVE is never selectable by default). */
  orderMode: OrderMode;
  /**
   * Whether the active chart is in bar replay. Trading features must not
   * place or change orders from a replayed chart.
   */
  replay?: boolean;
}

/** Capabilities known to the shell: `demo`, `market`, `strategy`, `execution`, `user-stream`, `venue:<id>:<capability>`. */
export interface CapabilitySet {
  /** Capability names present. */
  readonly names: ReadonlySet<string>;
}

/** Toast notifications. */
export interface Notifier {
  /**
   * Shows a notification.
   * @param message Plain-language message.
   * @param tone Visual tone.
   */
  notify(
    message: string,
    tone?: 'info' | 'success' | 'warning' | 'error'
  ): void;
}

/** Per-feature persisted settings with schema versioning. */
export interface FeatureSettingsStore {
  /**
   * Reads settings, migrating older versions via the provided migrator.
   * @param fallback Defaults when nothing is stored.
   * @param migrate Converts a stored value of an older version.
   * @return Settings value.
   */
  read<T>(fallback: T, migrate?: (stored: unknown, version: number) => T): T;
  /**
   * Writes settings tagged with the manifest's settings version.
   * @param value Settings value.
   */
  write<T>(value: T): void;
}

/** Overlay layers a feature publishes per chart. */
export interface ChartLayerRegistry {
  /**
   * Publishes a dynamic overlay layer provider. The chart host calls
   * `provide` for each mounted chart and re-renders whenever the returned
   * store changes.
   * @param provider Builds a layer store for a chart.
   * @return Disposer removing the layer from all charts.
   */
  register(
    provider: (chart: {
      chartId: string;
      instrumentId: InstrumentId;
      timeframe: Timeframe;
      atMs?: number | null;
    }) => ReadableStore<OverlayLayer | null>
  ): Disposer;
}

/** Everything a feature may use; nothing else is reachable. */
export interface FeatureContext<TView> {
  /** Activating feature id. */
  readonly featureId: string;
  /** Injected backend clients (HTTP or DEMO). */
  readonly clients: BackendClients;
  /** Shared selection (read) plus a narrow writer for selection intents. */
  readonly selection: ReadableStore<TerminalSelection>;
  /**
   * Requests a selection change (e.g. clicking a watchlist row).
   * @param patch Fields to change.
   */
  select(
    patch: Partial<
      Pick<TerminalSelection, 'instrumentId' | 'timeframe' | 'accountId'>
    >
  ): void;
  /** Capabilities. */
  readonly capabilities: ReadableStore<CapabilitySet>;
  /** Commands. */
  readonly commands: CommandRegistry;
  /** Hotkeys. */
  readonly hotkeys: HotkeyRegistry;
  /** UI slots. */
  readonly slots: SlotRegistry<TView>;
  /** Chart overlay layers. */
  readonly chartLayers: ChartLayerRegistry;
  /** Event bus. */
  readonly events: EventBus;
  /** Notifications. */
  readonly notifier: Notifier;
  /**
   * Session calendars (exchange sessions from the engine or DEMO) for
   * calendar-aware bar boundaries and market hours.
   */
  readonly calendars: SessionCalendarCache;
  /** Persisted settings for this feature. */
  readonly settings: FeatureSettingsStore;
}

/** A vetted terminal feature. */
export interface TerminalFeature<TView> {
  /** Static manifest. */
  readonly manifest: FeatureManifest;
  /**
   * Activates the feature.
   * @param ctx Context.
   * @return Disposer (mandatory) removing everything the feature registered.
   */
  activate(ctx: FeatureContext<TView>): Disposer | Promise<Disposer>;
}

/** Registry entry state observable by the shell (e.g. a features panel). */
export interface FeatureStatus {
  /** Feature id. */
  id: string;
  /** Effective lifecycle state. */
  lifecycle: FeatureLifecycle;
  /** Why the feature is not enabled, if applicable. */
  reason: string | null;
}

/** Writable store alias re-exported for hosts. */
export type FeatureStatusStore = WritableStore<readonly FeatureStatus[]>;
