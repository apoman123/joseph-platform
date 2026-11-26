/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for the feature registry lifecycle and host registries.
 */

import type {BackendClients} from '@joseph/contracts';
import {describe, expect, it, vi} from 'vitest';
import {
  comboFromEvent,
  createChartLayerRegistry,
  createCommandRegistry,
  createEventBus,
  createHotkeyRegistry,
  createSettingsStore,
  createSlotRegistry,
  normalizeCombo,
} from './registries';
import {FeatureRegistry, type FeatureHost} from './registry';
import {BrowserStorage, MemoryStorage} from './storage';
import {createStore} from './store';
import type {
  CapabilitySet,
  FeatureManifest,
  TerminalFeature,
  TerminalSelection,
} from './types';

/**
 * Builds a host with in-memory registries.
 * @param capabilities Capability names present.
 * @return Host plus its registries and capability store, for assertions.
 */
function makeHost(capabilities: string[] = []) {
  const commands = createCommandRegistry();
  const slots = createSlotRegistry<string>();
  const events = createEventBus();
  const chartLayers = createChartLayerRegistry();
  const caps = createStore<CapabilitySet>({names: new Set(capabilities)});
  const selection = createStore<TerminalSelection>({
    chartId: 'c1',
    instrumentId: 'mock:perp:BTCUSD',
    timeframe: '5m',
    feedMode: 'AUTO',
    accountId: null,
    orderMode: 'PAPER',
  });
  const host: FeatureHost<string> = {
    // Deliberate partial mock: these features never call a backend client.
    clients: {} as unknown as BackendClients,
    selection,
    select: patch => void selection.update(s => ({...s, ...patch})),
    capabilities: caps,
    commands,
    hotkeys: createHotkeyRegistry(commands),
    slots,
    chartLayers,
    events,
    notifier: {notify: () => undefined},
    storage: new MemoryStorage(),
  };
  return {host, commands, slots, events, chartLayers, caps};
}

/**
 * Builds a valid manifest.
 * @param id Feature id.
 * @param extra Fields that override the defaults.
 * @return Manifest.
 */
function manifest(
  id: string,
  extra: Partial<FeatureManifest> = {}
): FeatureManifest {
  return {
    id,
    version: '1.0.0',
    owner: 'test',
    displayName: id,
    description: 'test feature',
    uiSlots: ['side-panel'],
    settingsSchemaVersion: 1,
    contractVersion: 1,
    removal: {notes: 'No persisted data.'},
    ...extra,
  };
}

/**
 * Builds a feature that registers a command, slot, listener and layer but
 * returns a disposer that forgets to remove them (leak simulation).
 * @param id Feature id.
 * @param extra Manifest overrides.
 * @return Feature.
 */
function leakyFeature(
  id: string,
  extra: Partial<FeatureManifest> = {}
): TerminalFeature<string> {
  return {
    manifest: manifest(id, extra),
    activate(ctx) {
      ctx.commands.register({id: `${id}.run`, title: id, run: () => undefined});
      ctx.slots.contribute('side-panel', {
        id,
        title: id,
        order: 1,
        view: 'view',
      });
      ctx.events.on('ticket.open', () => undefined);
      ctx.chartLayers.register(() => createStore(null));
      return () => undefined;
    },
  };
}

describe('FeatureRegistry', () => {
  it('does not register a synchronous feature twice during overlapping activation', async () => {
    const {host, commands} = makeHost();
    const registry = new FeatureRegistry(host, {contractVersion: 1});
    registry.register(leakyFeature('once'));
    await Promise.all([registry.activateAll(), registry.activateAll()]);
    expect(
      commands.list().filter(command => command.id === 'once.run')
    ).toHaveLength(1);
    registry.deactivateAll();
    expect(commands.list()).toHaveLength(0);
  });

  it('rejects duplicate ids and manifests without a removal plan', () => {
    const {host} = makeHost();
    const registry = new FeatureRegistry(host, {contractVersion: 1});
    registry.register(leakyFeature('a'));
    expect(() => void registry.register(leakyFeature('a'))).toThrow(
      /Duplicate/
    );
    expect(
      () =>
        void registry.register({
          manifest: {...manifest('b'), removal: {notes: ''}},
          activate: () => () => undefined,
        })
    ).toThrow(/removal plan/);
  });

  it('disposes every tracked registration even if the feature forgets', async () => {
    const {host, commands, slots, chartLayers} = makeHost();
    const registry = new FeatureRegistry(host, {contractVersion: 1});
    registry.register(leakyFeature('a'));
    await registry.activateAll();
    expect(commands.list().map(c => c.id)).toEqual(['a.run']);
    expect(slots.observe('side-panel').get()).toHaveLength(1);
    expect(chartLayers.providers.get()).toHaveLength(1);
    registry.deactivate('a');
    expect(commands.list()).toEqual([]);
    expect(slots.observe('side-panel').get()).toEqual([]);
    expect(chartLayers.providers.get()).toEqual([]);
    await registry.activate('a');
    expect(commands.list()).toHaveLength(1);
  });

  it('activates dependencies first and deactivates dependents first', async () => {
    const {host} = makeHost();
    const order: string[] = [];
    const feature = (id: string, deps: string[] = []) => ({
      manifest: manifest(id, {dependencies: deps}),
      activate() {
        order.push(`+${id}`);
        return () => order.push(`-${id}`);
      },
    });
    const registry = new FeatureRegistry(host, {contractVersion: 1});
    registry.registerAll([feature('child', ['base']), feature('base')]);
    await registry.activateAll();
    registry.deactivate('base');
    expect(order).toEqual(['+base', '+child', '-child', '-base']);
  });

  it('detects cycles, missing deps and contract mismatches', async () => {
    const {host} = makeHost();
    const registry = new FeatureRegistry(host, {contractVersion: 1});
    registry.registerAll([
      leakyFeature('x', {dependencies: ['y']}),
      leakyFeature('y', {dependencies: ['x']}),
      leakyFeature('z', {dependencies: ['ghost']}),
      leakyFeature('old', {contractVersion: 0}),
    ]);
    const kinds = registry.validate().map(i => `${i.id}:${i.kind}`);
    expect(kinds).toEqual(
      expect.arrayContaining([
        'x:cycle',
        'y:cycle',
        'z:missing-dependency',
        'old:contract-version',
      ])
    );
    await registry.activateAll();
    expect(
      registry.status.get().every(s => s.lifecycle === 'UNAVAILABLE')
    ).toBe(true);
  });

  it('gates on capabilities and re-evaluates on refresh', async () => {
    const {host, caps} = makeHost();
    const registry = new FeatureRegistry(host, {contractVersion: 1});
    registry.register(
      leakyFeature('trade', {requiredCapabilities: ['execution']})
    );
    await registry.activateAll();
    expect(registry.isActive('trade')).toBe(false);
    expect(registry.status.get()[0]?.reason).toMatch(/execution/);
    caps.set({names: new Set(['execution'])});
    await registry.refresh();
    expect(registry.isActive('trade')).toBe(true);
    caps.set({names: new Set()});
    await registry.refresh();
    expect(registry.isActive('trade')).toBe(false);
  });

  it('keeps REMOVED and DISABLED features inactive with their reason', async () => {
    const {host} = makeHost();
    const registry = new FeatureRegistry(host, {contractVersion: 1});
    registry.registerAll([
      leakyFeature('fib', {
        lifecycle: 'REMOVED',
        removal: {notes: 'Saved fib drawings stay readable.'},
      }),
      leakyFeature('off', {lifecycle: 'DISABLED'}),
      leakyFeature('legacy', {
        lifecycle: 'DEPRECATED',
        removal: {notes: 'n', replacement: 'modern'},
      }),
    ]);
    await registry.activateAll();
    const byId = Object.fromEntries(registry.status.get().map(s => [s.id, s]));
    expect(byId.fib?.lifecycle).toBe('REMOVED');
    expect(byId.fib?.reason).toMatch(/stay readable/);
    expect(byId.off?.lifecycle).toBe('DISABLED');
    expect(byId.legacy?.lifecycle).toBe('DEPRECATED');
    expect(registry.isActive('legacy')).toBe(true);
  });

  it('isolates activation failures and rolls back partial registrations', async () => {
    const {host, commands} = makeHost();
    const registry = new FeatureRegistry(host, {contractVersion: 1});
    registry.register({
      manifest: manifest('broken'),
      activate(ctx) {
        ctx.commands.register({id: 'broken.cmd', title: 'b', run: () => {}});
        throw new Error('boom');
      },
    });
    registry.register(leakyFeature('ok'));
    await registry.activateAll();
    expect(registry.isActive('ok')).toBe(true);
    expect(commands.list().map(c => c.id)).toEqual(['ok.run']);
    expect(registry.status.get().find(s => s.id === 'broken')?.reason).toMatch(
      /boom/
    );
  });

  it('supports user disable and re-enable', async () => {
    const {host} = makeHost();
    const registry = new FeatureRegistry(host, {contractVersion: 1});
    registry.register(leakyFeature('a'));
    await registry.activateAll();
    await registry.setEnabled('a', false);
    expect(registry.isActive('a')).toBe(false);
    await registry.setEnabled('a', true);
    expect(registry.isActive('a')).toBe(true);
  });
});

describe('host registries', () => {
  it('normalizes hotkey combos', () => {
    expect(normalizeCombo('Ctrl+Shift+Z')).toBe('shift+mod+z');
    expect(normalizeCombo('cmd+k')).toBe('mod+k');
    expect(
      comboFromEvent({
        key: 'K',
        shiftKey: false,
        altKey: false,
        ctrlKey: false,
        metaKey: true,
        target: null,
      })
    ).toBe('mod+k');
  });

  it('runs bound commands but ignores typing targets', async () => {
    const commands = createCommandRegistry();
    const run = vi.fn();
    commands.register({id: 'palette', title: 'Palette', run});
    const hotkeys = createHotkeyRegistry(commands);
    const dispose = hotkeys.bind('mod+k', 'palette');
    const event = {
      key: 'k',
      shiftKey: false,
      altKey: false,
      ctrlKey: true,
      metaKey: false,
    };
    expect(hotkeys.handle({...event, target: null})).toBe(true);
    const input = document.createElement('input');
    expect(hotkeys.handle({...event, target: input})).toBe(false);
    await Promise.resolve();
    expect(run).toHaveBeenCalledTimes(1);
    dispose();
    expect(hotkeys.handle({...event, target: null})).toBe(false);
    expect(() => hotkeys.bind('mod+k', 'x')).not.toThrow();
  });

  it('orders slot contributions and rejects duplicates', () => {
    const slots = createSlotRegistry<string>();
    slots.contribute('bottom-panel', {
      id: 'b',
      title: 'B',
      order: 2,
      view: 'b',
    });
    slots.contribute('bottom-panel', {
      id: 'a',
      title: 'A',
      order: 1,
      view: 'a',
    });
    expect(
      slots
        .observe('bottom-panel')
        .get()
        .map(c => c.id)
    ).toEqual(['a', 'b']);
    expect(() =>
      slots.contribute('bottom-panel', {
        id: 'a',
        title: 'A',
        order: 3,
        view: '',
      })
    ).toThrow();
  });

  it('isolates failing event handlers', () => {
    const bus = createEventBus();
    const good = vi.fn();
    bus.on('ticket.open', () => {
      throw new Error('bad');
    });
    bus.on('ticket.open', good);
    bus.emit('ticket.open', {instrumentId: 'mock:perp:BTCUSD'});
    expect(good).toHaveBeenCalledOnce();
  });

  it('migrates versioned settings', () => {
    const storage = new MemoryStorage();
    createSettingsStore(storage, 'f', 1).write({color: 'red'});
    const v2 = createSettingsStore(storage, 'f', 2);
    expect(v2.read({tone: 'none'})).toEqual({tone: 'none'});
    expect(
      v2.read({tone: 'none'}, stored => ({
        tone: (stored as {color: string}).color,
      }))
    ).toEqual({tone: 'red'});
    expect(createSettingsStore(storage, 'f', 2).read({tone: 'x'})).toEqual({
      tone: 'red',
    });
  });

  it('browser storage never throws when blocked', () => {
    const storage = new BrowserStorage(() => {
      throw new Error('SecurityError');
    });
    expect(storage.getItem('k')).toBeNull();
    expect(() => void storage.setItem('k', 'v')).not.toThrow();
    expect(storage.keys('')).toEqual([]);
  });
});
