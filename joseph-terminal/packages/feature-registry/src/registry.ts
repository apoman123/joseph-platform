/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview `FeatureRegistry`: build-time composed feature lifecycle.
 *
 * Responsibilities: unique ids, dependency ordering, cycle and contract
 * compatibility checks, capability gating, lifecycle states, and leak-proof
 * activation. Every registration a feature makes through its context is
 * tracked and disposed on deactivation even if the feature's own disposer
 * forgets to remove it.
 */

import {SessionCalendarCache, type BackendClients} from '@joseph/contracts';
import {createSettingsStore, type ChartLayerHost} from './registries';
import type {KeyValueStorage} from './storage';
import {createStore, type Disposer, type ReadableStore} from './store';
import type {
  CapabilitySet,
  CommandRegistry,
  EventBus,
  FeatureContext,
  FeatureLifecycle,
  FeatureStatus,
  HotkeyRegistry,
  Notifier,
  SlotRegistry,
  TerminalFeature,
  TerminalSelection,
} from './types';

/** Shared services the host provides to every feature. */
export interface FeatureHost<TView> {
  /** Backend clients (HTTP or DEMO). */
  clients: BackendClients;
  /** Selection read model. */
  selection: ReadableStore<TerminalSelection>;
  /**
   * Applies a selection intent.
   * @param patch Fields to change.
   */
  select(
    patch: Partial<
      Pick<TerminalSelection, 'instrumentId' | 'timeframe' | 'accountId'>
    >
  ): void;
  /** Capabilities. */
  capabilities: ReadableStore<CapabilitySet>;
  /** Command registry. */
  commands: CommandRegistry;
  /** Hotkey registry. */
  hotkeys: HotkeyRegistry;
  /** Slot registry. */
  slots: SlotRegistry<TView>;
  /** Chart overlay layer registry. */
  chartLayers: ChartLayerHost;
  /** Event bus. */
  events: EventBus;
  /** Notifier. */
  notifier: Notifier;
  /** Storage for feature settings. */
  storage: KeyValueStorage;
  /**
   * Session calendars shared by every feature (defaults to a cache over
   * `clients.market.getSessions`).
   */
  calendars?: SessionCalendarCache;
}

/** Structural problem detected in the feature graph. */
export interface FeatureGraphIssue {
  /** Feature id. */
  id: string;
  /** Problem kind. */
  kind:
    'missing-dependency' | 'cycle' | 'contract-version' | 'missing-capability';
  /** Human-readable explanation. */
  message: string;
}

/** Options for the registry. */
export interface FeatureRegistryOptions {
  /** Major version of `@joseph/contracts` the host ships. */
  contractVersion: number;
}

/** Bookkeeping for one active feature. */
interface ActiveFeature<TView> {
  /** Scoped context handed to the feature. */
  ctx: FeatureContext<TView>;
  /** Disposer returned by the feature. */
  disposer: Disposer;
  /** Every registration made through the scoped context. */
  tracked: Disposer[];
}

/**
 * Returns whether a declared lifecycle state never activates.
 * @param lifecycle Declared lifecycle.
 * @return True for DISABLED, REMOVED and UNAVAILABLE.
 */
function isInactiveLifecycle(lifecycle: FeatureLifecycle): boolean {
  return (
    lifecycle === 'DISABLED' ||
    lifecycle === 'REMOVED' ||
    lifecycle === 'UNAVAILABLE'
  );
}

/** Registry of terminal features. */
export class FeatureRegistry<TView> {
  private readonly features = new Map<string, TerminalFeature<TView>>();
  private readonly active = new Map<string, ActiveFeature<TView>>();
  private readonly userDisabled = new Set<string>();
  private readonly statusStore = createStore<readonly FeatureStatus[]>([]);
  /** Activation error message per feature id, until it activates. */
  private readonly failures = new Map<string, string>();

  /**
   * Creates the registry.
   * @param host Shared services.
   * @param options Registry options.
   */
  constructor(
    private readonly host: FeatureHost<TView>,
    private readonly options: FeatureRegistryOptions
  ) {}

  /**
   * Observable status of every registered feature.
   * @return Status store.
   */
  get status(): ReadableStore<readonly FeatureStatus[]> {
    return this.statusStore;
  }

  /**
   * Registers a feature (build-time composition).
   * @param feature Feature to register.
   * @throws `Error` when the manifest lacks an id, a version or removal
   *     notes, or the id is already registered.
   */
  register(feature: TerminalFeature<TView>): void {
    const {id, version, removal} = feature.manifest;
    if (!id || !version) {
      throw new Error('Feature manifest needs id and version');
    }
    if (!removal?.notes) {
      throw new Error(`Feature ${id} must declare a removal plan`);
    }
    if (this.features.has(id)) throw new Error(`Duplicate feature id: ${id}`);
    this.features.set(id, feature);
    this.publishStatus();
  }

  /**
   * Registers several features in order.
   * @param features Features to register.
   * @throws `Error` from `register` for the first invalid or duplicate
   *     feature; the features before it stay registered.
   */
  registerAll(features: ReadonlyArray<TerminalFeature<TView>>): void {
    for (const feature of features) {
      this.register(feature);
    }
  }

  /**
   * Returns registered feature ids.
   * @return Ids in registration order.
   */
  ids(): string[] {
    return [...this.features.keys()];
  }

  /**
   * Returns whether a feature is currently active.
   * @param id Feature id.
   * @return True when active.
   */
  isActive(id: string): boolean {
    return this.active.has(id);
  }

  /**
   * Returns the scoped context of an active feature (hosts pass it to the
   * feature's contributed views).
   * @param id Feature id.
   * @return Context, or undefined when inactive.
   */
  contextOf(id: string): FeatureContext<TView> | undefined {
    return this.active.get(id)?.ctx;
  }

  /**
   * Detects structural issues: missing dependencies, cycles, incompatible
   * contract versions and missing capabilities.
   * @return Issues found (empty when the graph is healthy).
   */
  validate(): FeatureGraphIssue[] {
    const issues: FeatureGraphIssue[] = [];
    const capabilities = this.host.capabilities.get().names;
    for (const feature of this.features.values()) {
      const {id, dependencies = [], contractVersion} = feature.manifest;
      for (const dep of dependencies) {
        if (!this.features.has(dep)) {
          issues.push({
            id,
            kind: 'missing-dependency',
            message: `Depends on ${dep}, which is not registered`,
          });
        }
      }
      if (contractVersion !== this.options.contractVersion) {
        issues.push({
          id,
          kind: 'contract-version',
          message: `Targets contracts v${contractVersion}; host ships v${this.options.contractVersion}`,
        });
      }
      for (const capability of feature.manifest.requiredCapabilities ?? []) {
        if (!capabilities.has(capability)) {
          issues.push({
            id,
            kind: 'missing-capability',
            message: `Needs ${capability}`,
          });
        }
      }
    }
    for (const id of this.findCycles()) {
      issues.push({id, kind: 'cycle', message: 'Part of a dependency cycle'});
    }
    return issues;
  }

  /**
   * Returns ids participating in dependency cycles.
   * @return Set of ids.
   */
  private findCycles(): Set<string> {
    const inCycle = new Set<string>();
    const state = new Map<string, 'visiting' | 'done'>();
    const stack: string[] = [];
    const visit = (id: string): void => {
      state.set(id, 'visiting');
      stack.push(id);
      for (const dep of this.features.get(id)?.manifest.dependencies ?? []) {
        if (!this.features.has(dep)) continue;
        if (state.get(dep) === 'visiting') {
          for (const member of stack.slice(stack.indexOf(dep))) {
            inCycle.add(member);
          }
        } else if (!state.has(dep)) {
          visit(dep);
        }
      }
      stack.pop();
      state.set(id, 'done');
    };
    for (const id of this.features.keys()) {
      if (!state.has(id)) visit(id);
    }
    return inCycle;
  }

  /**
   * Returns ids in dependency order (dependencies first), skipping cycles.
   * @return Ordered ids.
   */
  activationOrder(): string[] {
    const cycles = this.findCycles();
    const order: string[] = [];
    const seen = new Set<string>();
    const visit = (id: string): void => {
      if (seen.has(id) || cycles.has(id) || !this.features.has(id)) return;
      seen.add(id);
      for (const dep of this.features.get(id)?.manifest.dependencies ?? []) {
        visit(dep);
      }
      order.push(id);
    };
    for (const id of this.features.keys()) {
      visit(id);
    }
    return order;
  }

  /**
   * Computes why a feature cannot be active, if anything prevents it.
   * @param id Feature id.
   * @return Lifecycle and reason when blocked, otherwise null.
   */
  private blocker(
    id: string
  ): {lifecycle: FeatureLifecycle; reason: string} | null {
    const feature = this.features.get(id);
    if (!feature) return {lifecycle: 'UNAVAILABLE', reason: 'Not registered'};
    const manifest = feature.manifest;
    const declared = manifest.lifecycle ?? 'ENABLED';
    if (declared === 'REMOVED') {
      return {
        lifecycle: 'REMOVED',
        reason: `Removed. ${manifest.removal.notes}`,
      };
    }
    if (declared === 'DISABLED' || this.userDisabled.has(id)) {
      return {lifecycle: 'DISABLED', reason: 'Turned off'};
    }
    if (declared === 'UNAVAILABLE') {
      return {lifecycle: 'UNAVAILABLE', reason: 'Declared unavailable'};
    }
    const issues = this.validate().filter(issue => issue.id === id);
    if (issues.length > 0) {
      return {
        lifecycle: 'UNAVAILABLE',
        reason: issues.map(issue => issue.message).join('; '),
      };
    }
    for (const dep of manifest.dependencies ?? []) {
      if (!this.active.has(dep)) {
        return {lifecycle: 'UNAVAILABLE', reason: `Waiting for ${dep}`};
      }
    }
    return null;
  }

  /**
   * Activates every eligible feature in dependency order. Failures in one
   * feature are isolated and reported in its status.
   * @return Resolves when activation finished.
   */
  async activateAll(): Promise<void> {
    for (const id of this.activationOrder()) {
      await this.activate(id);
    }
    this.publishStatus();
  }

  /**
   * Activates one feature if it is eligible and not active.
   *
   * An activation error is caught: the feature's registrations so far are
   * disposed and the error message becomes its status reason.
   * @param id Feature id.
   * @return True when the feature is active afterwards.
   */
  async activate(id: string): Promise<boolean> {
    if (this.active.has(id)) return true;
    const feature = this.features.get(id);
    if (!feature || this.blocker(id)) {
      this.publishStatus();
      return false;
    }
    const tracked: Disposer[] = [];
    const ctx = this.scopedContext(feature, tracked);
    try {
      // Synchronous features must become active before another activation
      // pass can run (for example during React Strict Mode effect replay).
      const result = feature.activate(ctx);
      const disposer = result instanceof Promise ? await result : result;
      if (typeof disposer !== 'function') {
        throw new Error(`Feature ${id} did not return a disposer`);
      }
      this.active.set(id, {ctx, disposer, tracked});
    } catch (error) {
      for (const dispose of tracked.reverse()) {
        safeDispose(dispose);
      }
      this.failures.set(id, describeError(error));
      this.publishStatus();
      return false;
    }
    this.failures.delete(id);
    this.publishStatus();
    return true;
  }

  /**
   * Deactivates a feature and, first, every active feature depending on it.
   * @param id Feature id.
   */
  deactivate(id: string): void {
    for (const [otherId, other] of this.features) {
      if (
        this.active.has(otherId) &&
        (other.manifest.dependencies ?? []).includes(id)
      ) {
        this.deactivate(otherId);
      }
    }
    const entry = this.active.get(id);
    if (!entry) return;
    this.active.delete(id);
    safeDispose(entry.disposer);
    for (const dispose of [...entry.tracked].reverse()) {
      safeDispose(dispose);
    }
    this.publishStatus();
  }

  /**
   * Turns a feature on or off at runtime (user preference).
   *
   * Turning off also deactivates its dependants; turning on runs
   * `activateAll`, so dependants that were waiting start too.
   * @param id Feature id.
   * @param enabled Desired state.
   * @return Resolves when the change is applied.
   */
  async setEnabled(id: string, enabled: boolean): Promise<void> {
    if (enabled) {
      this.userDisabled.delete(id);
      await this.activateAll();
    } else {
      this.userDisabled.add(id);
      this.deactivate(id);
    }
    this.publishStatus();
  }

  /**
   * Re-evaluates eligibility after capabilities change: deactivates active
   * features whose required capabilities are gone, then activates every
   * newly eligible one.
   * @return Resolves when done.
   */
  async refresh(): Promise<void> {
    for (const id of [...this.active.keys()]) {
      const feature = this.features.get(id);
      const missing = (feature?.manifest.requiredCapabilities ?? []).some(
        capability => !this.host.capabilities.get().names.has(capability)
      );
      if (missing) this.deactivate(id);
    }
    await this.activateAll();
  }

  /** Deactivates every feature (reverse dependency order). */
  deactivateAll(): void {
    for (const id of this.activationOrder().reverse()) {
      this.deactivate(id);
    }
  }

  /**
   * Builds a context whose registrations are tracked for disposal.
   *
   * On first use it also creates the host's shared session calendar cache
   * when the host did not provide one.
   * @param feature Feature being activated.
   * @param tracked Sink for idempotent disposers of every registration.
   * @return Scoped context.
   */
  private scopedContext(
    feature: TerminalFeature<TView>,
    tracked: Disposer[]
  ): FeatureContext<TView> {
    const host = this.host;
    if (!host.calendars) {
      // One cache shared by all features. Hosts without a market client
      // (tests pass partial clients, hence the optional chaining) get
      // regular hours only.
      const market = host.clients?.market as
        BackendClients['market'] | undefined;
      host.calendars = new SessionCalendarCache(
        market?.getSessions?.bind(market) ?? null
      );
    }
    const calendars = host.calendars;
    const track = (dispose: Disposer): Disposer => {
      let done = false;
      const once: Disposer = () => {
        if (done) return;
        done = true;
        dispose();
      };
      tracked.push(once);
      return once;
    };
    return {
      featureId: feature.manifest.id,
      clients: host.clients,
      selection: host.selection,
      select: patch => void host.select(patch),
      capabilities: host.capabilities,
      commands: {
        register: command => track(host.commands.register(command)),
        execute: (id, args) => host.commands.execute(id, args),
        list: () => host.commands.list(),
      },
      hotkeys: {bind: (combo, id) => track(host.hotkeys.bind(combo, id))},
      slots: {
        contribute: (slot, contribution) =>
          track(
            host.slots.contribute(slot, {
              ...contribution,
              featureId: feature.manifest.id,
            })
          ),
        observe: slot => host.slots.observe(slot),
      },
      chartLayers: {
        register: provider => track(host.chartLayers.register(provider)),
      },
      events: {
        emit: (type, payload) => void host.events.emit(type, payload),
        on: (type, handler) => track(host.events.on(type, handler)),
      },
      notifier: host.notifier,
      calendars,
      settings: createSettingsStore(
        host.storage,
        feature.manifest.id,
        feature.manifest.settingsSchemaVersion
      ),
    };
  }

  /** Recomputes and publishes feature statuses. */
  private publishStatus(): void {
    const statuses: FeatureStatus[] = [];
    for (const [id, feature] of this.features) {
      const failure = this.failures.get(id);
      if (this.active.has(id)) {
        const declared = feature.manifest.lifecycle ?? 'ENABLED';
        statuses.push({
          id,
          lifecycle: declared === 'DEPRECATED' ? 'DEPRECATED' : 'ENABLED',
          reason:
            declared === 'DEPRECATED'
              ? `Deprecated${feature.manifest.removal.replacement ? `; use ${feature.manifest.removal.replacement}` : ''}`
              : null,
        });
      } else if (failure) {
        statuses.push({id, lifecycle: 'UNAVAILABLE', reason: failure});
      } else {
        const blocked = this.blocker(id);
        statuses.push({
          id,
          lifecycle: blocked?.lifecycle ?? 'DISABLED',
          reason: blocked?.reason ?? 'Not activated yet',
        });
      }
    }
    this.statusStore.set(statuses);
  }
}

/**
 * Runs a disposer, swallowing errors so cleanup always continues.
 * @param dispose Disposer.
 */
function safeDispose(dispose: Disposer): void {
  try {
    dispose();
  } catch {
    // Cleanup of the remaining registrations must continue.
  }
}

/**
 * Formats an unknown error for status display.
 * @param error Thrown value.
 * @return Message.
 */
function describeError(error: unknown): string {
  return error instanceof Error
    ? `Failed to start: ${error.message}`
    : 'Failed to start';
}

/**
 * Returns whether a declared lifecycle state never activates (exported for
 * hosts and tests).
 * @param lifecycle Declared lifecycle.
 * @return True for DISABLED, REMOVED and UNAVAILABLE.
 */
export function lifecycleIsInactive(lifecycle: FeatureLifecycle): boolean {
  return isInactiveLifecycle(lifecycle);
}
