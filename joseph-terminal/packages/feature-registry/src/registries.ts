/**
 * @fileoverview Host-side registries: commands, hotkeys, slots, events,
 * settings and chart overlay layers.
 */
import type {OverlayLayer} from '@joseph/chart-core';
import type {InstrumentId, Timeframe} from '@joseph/contracts';
import {readJson, writeJson, type KeyValueStorage} from './storage';
import {createStore, type Disposer, type ReadableStore} from './store';
import type {
  ChartLayerRegistry,
  CommandRegistry,
  CommandSpec,
  EventBus,
  FeatureSettingsStore,
  HotkeyRegistry,
  SlotContribution,
  SlotRegistry,
  UiSlot,
} from './types';

/**
 * Creates a command registry.
 * @return Command registry.
 */
export function createCommandRegistry(): CommandRegistry {
  const commands = new Map<string, CommandSpec>();
  return {
    register(command) {
      if (commands.has(command.id)) {
        throw new Error(`Command already registered: ${command.id}`);
      }
      commands.set(command.id, command);
      return () => {
        if (commands.get(command.id) === command) commands.delete(command.id);
      };
    },
    async execute(id, args) {
      const command = commands.get(id);
      if (!command) throw new Error(`Unknown command: ${id}`);
      await command.run(args);
    },
    list() {
      return [...commands.values()].sort((a, b) =>
        a.title.localeCompare(b.title)
      );
    },
  };
}

/** Modifier order used by normalized combos. */
const MODIFIERS = ['shift', 'alt', 'mod'] as const;

/**
 * Normalizes a combo such as `Ctrl+Shift+Z` to `shift+mod+z`.
 * `ctrl`, `meta`, `cmd` and `mod` all map to `mod`.
 * @param combo Human-entered combo.
 * @return Canonical combo.
 */
export function normalizeCombo(combo: string): string {
  const parts = combo
    .toLowerCase()
    .split('+')
    .map(part => part.trim())
    .filter(Boolean);
  const mods = new Set<string>();
  let key = '';
  for (const part of parts) {
    if (['ctrl', 'control', 'meta', 'cmd', 'command', 'mod'].includes(part)) {
      mods.add('mod');
    } else if (part === 'option' || part === 'alt') {
      mods.add('alt');
    } else if (part === 'shift') {
      mods.add('shift');
    } else {
      key = part === 'esc' ? 'escape' : part;
    }
  }
  return [...MODIFIERS.filter(mod => mods.has(mod)), key].join('+');
}

/** Keyboard event subset used for combo detection. */
export interface KeyEventLike {
  /** Key value. */
  key: string;
  /** Shift pressed. */
  shiftKey: boolean;
  /** Alt pressed. */
  altKey: boolean;
  /** Ctrl pressed. */
  ctrlKey: boolean;
  /** Meta pressed. */
  metaKey: boolean;
  /** Event target. */
  target: EventTarget | null;
}

/**
 * Converts a keyboard event to a normalized combo.
 * @param event Keyboard event.
 * @return Canonical combo.
 */
export function comboFromEvent(event: KeyEventLike): string {
  const parts: string[] = [];
  if (event.shiftKey) parts.push('shift');
  if (event.altKey) parts.push('alt');
  if (event.ctrlKey || event.metaKey) parts.push('mod');
  parts.push(
    event.key.toLowerCase() === ' ' ? 'space' : event.key.toLowerCase()
  );
  return normalizeCombo(parts.join('+'));
}

/**
 * Whether a keyboard event originates from a text-entry element.
 * @param target Event target.
 * @return True for inputs, textareas, selects and contenteditable nodes.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!target || typeof target !== 'object') return false;
  const element = target as {
    tagName?: string;
    isContentEditable?: boolean;
  };
  const tag = element.tagName?.toLowerCase();
  return (
    tag === 'input' ||
    tag === 'textarea' ||
    tag === 'select' ||
    element.isContentEditable === true
  );
}

/** Hotkey registry that can be driven by DOM keydown events. */
export interface HotkeyHost extends HotkeyRegistry {
  /**
   * Handles a keydown event.
   * @param event Keyboard event.
   * @return True when a binding ran (callers should preventDefault).
   */
  handle(event: KeyEventLike): boolean;
  /**
   * Lists current bindings.
   * @return Map of combo to command id.
   */
  bindings(): ReadonlyMap<string, string>;
}

/**
 * Creates a hotkey registry bound to a command registry.
 * Combos are ignored while the user types into a field, except `escape`.
 * @param commands Command registry used to execute bindings.
 * @return Hotkey host.
 */
export function createHotkeyRegistry(commands: CommandRegistry): HotkeyHost {
  const bindings = new Map<string, string>();
  return {
    bind(combo, commandId) {
      const normalized = normalizeCombo(combo);
      if (bindings.has(normalized)) {
        throw new Error(`Hotkey already bound: ${normalized}`);
      }
      bindings.set(normalized, commandId);
      return () => {
        if (bindings.get(normalized) === commandId) bindings.delete(normalized);
      };
    },
    handle(event) {
      const combo = comboFromEvent(event);
      if (isTypingTarget(event.target) && combo !== 'escape') return false;
      const commandId = bindings.get(combo);
      if (!commandId) return false;
      void commands.execute(commandId).catch(() => undefined);
      return true;
    },
    bindings() {
      return bindings;
    },
  };
}

/**
 * Creates a slot registry.
 * @return Slot registry.
 */
export function createSlotRegistry<TView>(): SlotRegistry<TView> {
  const stores = new Map<
    UiSlot,
    ReturnType<typeof createStore<readonly SlotContribution<TView>[]>>
  >();
  const storeFor = (slot: UiSlot) => {
    let store = stores.get(slot);
    if (!store) {
      store = createStore<readonly SlotContribution<TView>[]>([]);
      stores.set(slot, store);
    }
    return store;
  };
  return {
    contribute(slot, contribution) {
      const store = storeFor(slot);
      if (store.get().some(item => item.id === contribution.id)) {
        throw new Error(`Duplicate contribution ${contribution.id} in ${slot}`);
      }
      store.update(items =>
        [...items, contribution].sort(
          (a, b) => a.order - b.order || a.id.localeCompare(b.id)
        )
      );
      return () => {
        store.update(items => items.filter(item => item !== contribution));
      };
    },
    observe(slot) {
      return storeFor(slot);
    },
  };
}

/**
 * Creates a typed event bus. Handler errors are isolated.
 * @return Event bus.
 */
export function createEventBus(): EventBus {
  const handlers = new Map<string, Set<(payload: never) => void>>();
  return {
    emit(type, payload) {
      for (const handler of [...(handlers.get(type) ?? [])]) {
        try {
          (handler as (value: typeof payload) => void)(payload);
        } catch {
          // One faulty subscriber must not break the others.
        }
      }
    },
    on(type, handler) {
      let set = handlers.get(type);
      if (!set) {
        set = new Set();
        handlers.set(type, set);
      }
      const typed = handler as (payload: never) => void;
      set.add(typed);
      return () => {
        set?.delete(typed);
      };
    },
  };
}

/** Stored settings envelope. */
interface SettingsEnvelope {
  /** Settings schema version. */
  version: number;
  /** Settings value. */
  value: unknown;
}

/**
 * Creates a versioned per-feature settings store.
 * @param storage Storage port.
 * @param featureId Owning feature id.
 * @param version Current settings schema version.
 * @return Settings store.
 */
export function createSettingsStore(
  storage: KeyValueStorage,
  featureId: string,
  version: number
): FeatureSettingsStore {
  const key = `joseph.settings.${featureId}`;
  return {
    read<T>(fallback: T, migrate?: (stored: unknown, from: number) => T): T {
      const stored = readJson(storage, key) as SettingsEnvelope | null;
      if (!stored || typeof stored !== 'object' || !('version' in stored)) {
        return fallback;
      }
      if (stored.version === version) return stored.value as T;
      if (migrate && stored.version < version) {
        try {
          const migrated = migrate(stored.value, stored.version);
          writeJson(storage, key, {version, value: migrated});
          return migrated;
        } catch {
          return fallback;
        }
      }
      return fallback;
    },
    write<T>(value: T): void {
      writeJson(storage, key, {version, value});
    },
  };
}

/** Identity of a mounted chart passed to layer providers. */
export interface ChartIdentity {
  /** Chart id. */
  chartId: string;
  /** Instrument on the chart. */
  instrumentId: InstrumentId;
  /** Timeframe on the chart. */
  timeframe: Timeframe;
  /**
   * Replay point-in-time (UTC ms) when the chart is in bar replay; layers
   * must then show only what the backend knew at that time.
   */
  atMs?: number | null;
}

/** Overlay layer provider registered by a feature. */
export type ChartLayerProvider = (
  chart: ChartIdentity
) => ReadableStore<OverlayLayer | null>;

/** Host view of the chart layer registry. */
export interface ChartLayerHost extends ChartLayerRegistry {
  /** Registered providers (observable). */
  readonly providers: ReadableStore<readonly ChartLayerProvider[]>;
}

/**
 * Creates a chart layer registry.
 * @return Chart layer host.
 */
export function createChartLayerRegistry(): ChartLayerHost {
  const providers = createStore<readonly ChartLayerProvider[]>([]);
  return {
    providers,
    register(provider): Disposer {
      providers.update(items => [...items, provider]);
      return () => {
        providers.update(items => items.filter(item => item !== provider));
      };
    },
  };
}
