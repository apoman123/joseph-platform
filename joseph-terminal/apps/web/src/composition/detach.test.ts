/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Main window and pop-out windows over an in-memory channel:
 * detaching, workspace/link-group/theme/crosshair sync, action forwarding,
 * re-docking on goodbye, closed handles and crashed windows, pop-outs that
 * survive a reload of the main window (probe, re-adoption, replay of
 * changes made meanwhile), "Restore pop-outs" after a browser restart with
 * remembered window geometry, and trading guards that never cross windows.
 */

import {
  DETACHED_PANEL_KEY,
  LIVE_TIMEOUT_MS,
  MemoryChannelHub,
  PROBE_MS,
  PopoutBus,
  activeChart,
  detachedStatus,
  dormantPanes,
  paneStatus,
  readDetachedPanes,
  type ChannelPort,
  type DetachedPaneRef,
  type PopoutMessage,
  type WindowGeometry,
} from '@joseph/terminal-shell';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {
  RESTORE_POPOUTS_COMMAND,
  popoutFeatures,
  popoutWindowName,
  restoreMessage,
  startMainDetach,
  startPopoutLink,
  type DetachController,
  type PopoutLink,
} from './detach';
import {createRuntime, type TerminalRuntime} from './runtime';

/** DEMO configuration. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

/** Fake window handle returned by `window.open`. */
interface FakeWindow {
  closed: boolean;
  focus: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
}

let clock = 1_800_000_000_000;
const cleanups: Array<() => void> = [];

beforeEach(() => {
  vi.useFakeTimers();
  clock = 1_800_000_000_000;
  localStorage.clear();
});

afterEach(() => {
  for (const cleanup of cleanups.splice(0).reverse()) {
    cleanup();
  }
  vi.useRealTimers();
});

/**
 * Advances fake time and the injected clock together.
 * @param ms Time to advance, in milliseconds.
 */
function advance(ms: number): void {
  clock += ms;
  vi.advanceTimersByTime(ms);
}

/** Options of a test main window. */
interface MainOptions {
  /** `window.open` is blocked (always, or per call). */
  blocked?: boolean | (() => boolean);
  /** Reuse a runtime (React StrictMode re-runs effects on one runtime). */
  runtime?: TerminalRuntime;
  /** Channel port (default: a new port on the hub). */
  port?: ChannelPort;
}

/**
 * Starts a main window.
 * @param hub Channel hub.
 * @param options Popup policy, runtime and port.
 * @return Runtime, controller, opened windows and their URLs.
 */
function mainWindow(hub: MemoryChannelHub, options: MainOptions = {}) {
  const runtime = options.runtime ?? createRuntime(() => [], DEMO);
  const opened: Array<{
    url: string;
    name: string;
    features: string;
    handle: FakeWindow;
  }> = [];
  const blocked = options.blocked ?? false;
  const controller: DetachController = startMainDetach(runtime, {
    port: options.port ?? hub.port(),
    open: (url, name, features) => {
      if (typeof blocked === 'function' ? blocked() : blocked) return null;
      const handle: FakeWindow = {
        closed: false,
        focus: vi.fn(),
        close: vi.fn(),
      };
      opened.push({url, name, features, handle});
      // Deliberate partial mock: the controller uses only these members.
      return handle as unknown as Window;
    },
    now: () => clock,
    windowId: `main-${Math.random()}`,
  });
  runtime.windows.set(controller.actions);
  cleanups.push(() => void controller.dispose());
  return {runtime, controller, opened, actions: controller.actions};
}

/**
 * Starts a pop-out window for a pane.
 * @param hub Channel hub.
 * @param ref Pane.
 * @param geometry Window position and size the pop-out reports.
 * @return Runtime, link and window callbacks.
 */
function popoutWindow(
  hub: MemoryChannelHub,
  ref: DetachedPaneRef,
  geometry: WindowGeometry | null = null
) {
  let link: PopoutLink | null = null;
  const runtime: TerminalRuntime = createRuntime(() => [], DEMO, {
    role: 'popout',
    pinnedChartId: ref.kind === 'chart' ? ref.chartId : null,
    onAction: action => link?.forward(action),
  });
  const close = vi.fn();
  const focus = vi.fn();
  link = startPopoutLink(
    runtime,
    {
      port: hub.port(),
      open: () => null,
      now: () => clock,
      windowId: `popout-${Math.random()}`,
    },
    {
      ref,
      title: () => 'pane',
      hidden: () => false,
      close,
      focus,
      geometry: () => geometry,
    }
  );
  const started = link;
  cleanups.push(() => void started.dispose());
  return {runtime, link: started, close, focus};
}

const CHART_1: DetachedPaneRef = {kind: 'chart', chartId: 'chart-1'};

describe('detaching a chart pane', () => {
  it('opens a named window, links it and remembers it in the layout', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    expect(main.opened).toHaveLength(1);
    expect(main.opened[0]?.url).toBe('/popout?pane=chart%3Achart-1&ws=default');
    expect(main.opened[0]?.name).toBe(popoutWindowName('chart:chart-1'));
    const key = 'chart:chart-1';
    expect(detachedStatus(main.runtime.detached.get().live[key], clock)).toBe(
      'opening'
    );
    expect(readDetachedPanes(main.runtime.workspace.get().panels)).toEqual([
      {key, ref: CHART_1, title: 'BTCUSD 5m'},
    ]);
    popoutWindow(hub, CHART_1);
    expect(detachedStatus(main.runtime.detached.get().live[key], clock)).toBe(
      'live'
    );
  });

  it('syncs the symbol from the main window and forwards pop-out changes', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    const pop = popoutWindow(hub, CHART_1);
    main.runtime.dispatch({
      type: 'setInstrument',
      chartId: 'chart-1',
      instrumentId: 'mock:perp:ETHUSD',
    });
    expect(activeChart(pop.runtime.workspace.get()).instrument_id).toBe(
      'mock:perp:ETHUSD'
    );
    pop.runtime.dispatch({
      type: 'setTimeframe',
      chartId: 'chart-1',
      timeframe: '1M',
    });
    expect(activeChart(main.runtime.workspace.get()).timeframe).toBe('1M');
    // The pop-out never writes the saved workspace itself.
    pop.runtime.flush();
    const saved = JSON.parse(
      localStorage.getItem('joseph.workspace.v1.default') ?? '{}'
    ) as {charts?: Array<{timeframe: string}>};
    main.runtime.flush();
    const after = JSON.parse(
      localStorage.getItem('joseph.workspace.v1.default') ?? '{}'
    ) as {charts?: Array<{timeframe: string}>};
    expect(saved.charts?.[0]?.timeframe).not.toBe('1M');
    expect(after.charts?.[0]?.timeframe).toBe('1M');
  });

  it('follows link groups into the pop-out', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.runtime.dispatch({type: 'setLayout', layout: '2h'});
    main.runtime.dispatch({type: 'setSync', key: 'instrument', value: true});
    for (const chartId of ['chart-1', 'chart-2']) {
      main.runtime.dispatch({type: 'setLinkGroup', chartId, group: 'A'});
    }
    const ref: DetachedPaneRef = {kind: 'chart', chartId: 'chart-2'};
    main.actions.detach(ref, 'BTCUSD 5m');
    const pop = popoutWindow(hub, ref);
    // The pop-out's own chart stays active there; the main window keeps its own.
    expect(pop.runtime.workspace.get().active_chart_id).toBe('chart-2');
    expect(main.runtime.workspace.get().active_chart_id).toBe('chart-1');
    main.runtime.dispatch({
      type: 'setInstrument',
      chartId: 'chart-1',
      instrumentId: 'mock:perp:SOLUSD',
    });
    expect(activeChart(pop.runtime.workspace.get()).instrument_id).toBe(
      'mock:perp:SOLUSD'
    );
  });

  it('syncs theme both ways and relays the crosshair without echo', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    const pop = popoutWindow(hub, CHART_1);
    main.runtime.theme.set('light');
    expect(pop.runtime.theme.get()).toBe('light');
    pop.runtime.theme.set('dark');
    expect(main.runtime.theme.get()).toBe('dark');
    const seenInPopout: unknown[] = [];
    const seenInMain: unknown[] = [];
    pop.runtime.crosshair.subscribe(p => seenInPopout.push(p));
    main.runtime.crosshair.subscribe(p => seenInMain.push(p));
    const point = {sourceChartId: 'chart-3', point: {timeMs: 1, price: 2}};
    main.runtime.crosshair.emit(point);
    expect(seenInPopout).toEqual([point]);
    expect(seenInMain).toEqual([point]);
  });
});

describe('re-docking', () => {
  it('docks the pane when the pop-out says goodbye', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    const pop = popoutWindow(hub, CHART_1);
    pop.link.goodbye();
    expect(main.runtime.detached.get().panes).toEqual([]);
    expect(readDetachedPanes(main.runtime.workspace.get().panels)).toEqual([]);
    expect(main.runtime.toasts.get().at(-1)?.message).toMatch(
      /back in this window/
    );
  });

  it('docks the pane when the window handle reports closed', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    popoutWindow(hub, CHART_1);
    const handle = main.opened[0]?.handle;
    if (handle) handle.closed = true;
    advance(1_000);
    expect(main.runtime.detached.get().panes).toEqual([]);
  });

  it('docks the pane when a crashed window stops sending heartbeats', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    const pop = popoutWindow(hub, CHART_1);
    const notify = vi.spyOn(main.runtime.notifier, 'notify');
    advance(6_000);
    expect(main.runtime.detached.get().panes).toHaveLength(1);
    pop.link.dispose(); // the window dies without a goodbye
    advance(LIVE_TIMEOUT_MS + 2_000);
    expect(main.runtime.detached.get().panes).toEqual([]);
    expect(notify).toHaveBeenLastCalledWith(
      expect.stringMatching(/stopped responding/),
      'info'
    );
  });

  it('closes the pop-out when the main window brings the pane back', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    const pop = popoutWindow(hub, CHART_1);
    main.actions.focus('chart:chart-1');
    expect(main.opened[0]?.handle.focus).toHaveBeenCalled();
    expect(pop.focus).toHaveBeenCalled();
    main.actions.redock('chart:chart-1');
    expect(pop.close).toHaveBeenCalled();
    expect(main.opened[0]?.handle.close).toHaveBeenCalled();
    expect(main.runtime.detached.get().panes).toEqual([]);
    // A heartbeat already in flight must not detach the pane again.
    advance(2_000);
    expect(main.runtime.detached.get().panes).toEqual([]);
  });
});

const ALERTS: DetachedPaneRef = {
  kind: 'panel',
  slot: 'bottom-panel',
  panelId: 'alerts',
};

/**
 * Returns the status of every detached pane of a main window.
 * @param runtime Main runtime.
 * @return Status per pane, in detach order.
 */
function statuses(runtime: TerminalRuntime): string[] {
  const state = runtime.detached.get();
  return state.panes.map(p => paneStatus(state, p.key, clock));
}

describe('after a browser restart', () => {
  it('probes, then offers the remembered panes for restoring', () => {
    const hub = new MemoryChannelHub();
    const first = mainWindow(hub);
    first.actions.detach(CHART_1, 'BTCUSD 5m');
    first.actions.detach(ALERTS, 'Alerts');
    first.runtime.flush();
    first.controller.dispose();
    const reloaded = mainWindow(new MemoryChannelHub());
    const state = reloaded.runtime.detached.get();
    expect(state.panes.map(p => p.key)).toEqual([
      'chart:chart-1',
      'panel:alerts',
    ]);
    // No "restore" offer while windows that kept running may still answer.
    expect(statuses(reloaded.runtime)).toEqual([
      'reconnecting',
      'reconnecting',
    ]);
    expect(dormantPanes(state, clock)).toEqual([]);
    reloaded.actions.reopenAll();
    expect(reloaded.opened).toEqual([]);
    expect(reloaded.runtime.toasts.get().at(-1)?.message).toMatch(
      /Still looking/
    );
    advance(PROBE_MS);
    expect(statuses(reloaded.runtime)).toEqual(['dormant', 'dormant']);
    // Dormant panes are never re-docked by the liveness timer.
    advance(60_000);
    expect(reloaded.runtime.detached.get().panes).toHaveLength(2);
    reloaded.actions.reopenAll();
    expect(reloaded.opened.map(o => o.url)).toEqual([
      '/popout?pane=chart%3Achart-1&ws=default',
      '/popout?pane=panel%3Aalerts&ws=default&slot=bottom-panel',
    ]);
  });

  it('restores from one command and explains the browser limit', async () => {
    const first = mainWindow(new MemoryChannelHub());
    first.actions.detach(CHART_1, 'BTCUSD 5m');
    first.actions.detach(ALERTS, 'Alerts');
    first.runtime.flush();
    first.controller.dispose();
    // Without pop-up permission the browser opens one window per click.
    let allowed = 0;
    const restarted = mainWindow(new MemoryChannelHub(), {
      blocked: () => {
        allowed -= 1;
        return allowed < 0;
      },
    });
    advance(PROBE_MS);
    const command = restarted.runtime.commands
      .list()
      .find(c => c.id === RESTORE_POPOUTS_COMMAND);
    expect(command?.title).toBe('Restore pop-outs');
    allowed = 1;
    await restarted.runtime.commands.execute(RESTORE_POPOUTS_COMMAND);
    expect(restarted.opened).toHaveLength(1);
    expect(restarted.runtime.toasts.get().at(-1)?.message).toBe(
      restoreMessage(1, 2)
    );
    allowed = 1;
    await restarted.runtime.commands.execute(RESTORE_POPOUTS_COMMAND);
    expect(restarted.opened.map(o => o.url)).toEqual([
      '/popout?pane=chart%3Achart-1&ws=default',
      '/popout?pane=panel%3Aalerts&ws=default&slot=bottom-panel',
    ]);
    await restarted.runtime.commands.execute(RESTORE_POPOUTS_COMMAND);
    expect(restarted.runtime.toasts.get().at(-1)?.message).toMatch(
      /already has its window/
    );
    expect(restoreMessage(2, 2)).toBeNull();
    expect(restoreMessage(0, 2)).toMatch(/blocked the windows/);
  });

  it('reopens windows where they were', () => {
    const hub = new MemoryChannelHub();
    const first = mainWindow(hub);
    first.actions.detach(CHART_1, 'BTCUSD 5m');
    const geometry = {left: 30, top: 40, width: 900, height: 640};
    const pop = popoutWindow(hub, CHART_1, geometry);
    expect(readDetachedPanes(first.runtime.workspace.get().panels)).toEqual([
      {key: 'chart:chart-1', ref: CHART_1, title: 'BTCUSD 5m', geometry},
    ]);
    first.runtime.flush();
    first.controller.dispose();
    pop.link.dispose();
    const restarted = mainWindow(new MemoryChannelHub());
    advance(PROBE_MS);
    restarted.actions.reopenAll();
    expect(restarted.opened[0]?.features).toBe(
      'popup=yes,width=900,height=640,left=30,top=40'
    );
    expect(popoutFeatures(ALERTS)).toBe('popup=yes,width=520,height=720');
  });
});

describe('main window reload', () => {
  /**
   * Detaches chart-1 into a pop-out, then "reloads" the main window: the
   * old controller says goodbye and goes away with its saved workspace.
   * @return Hub, pop-out and the first main window.
   */
  function reloadWithPopout() {
    const hub = new MemoryChannelHub();
    const first = mainWindow(hub);
    first.actions.detach(CHART_1, 'BTCUSD 5m');
    const pop = popoutWindow(hub, CHART_1);
    first.runtime.flush();
    first.controller.goodbye();
    first.controller.dispose();
    return {hub, pop, first};
  }

  it('re-adopts surviving pop-outs without a click or a restore offer', () => {
    const {hub, pop} = reloadWithPopout();
    expect(pop.link.main.get()).toBe('reconnecting');
    const reloaded = mainWindow(hub);
    expect(statuses(reloaded.runtime)).toEqual(['live']);
    expect(dormantPanes(reloaded.runtime.detached.get(), clock)).toEqual([]);
    expect(pop.link.main.get()).toBe('linked');
    advance(PROBE_MS + 4_000);
    expect(statuses(reloaded.runtime)).toEqual(['live']);
    // The new main window keeps both windows in step again.
    reloaded.runtime.dispatch({
      type: 'setInstrument',
      chartId: 'chart-1',
      instrumentId: 'mock:perp:ETHUSD',
    });
    expect(activeChart(pop.runtime.workspace.get()).instrument_id).toBe(
      'mock:perp:ETHUSD'
    );
    pop.runtime.dispatch({
      type: 'setTimeframe',
      chartId: 'chart-1',
      timeframe: '4h',
    });
    expect(activeChart(reloaded.runtime.workspace.get()).timeframe).toBe('4h');
    expect(pop.link.pending.get()).toBe(0);
  });

  it('replays changes made in a pop-out while the main window reloaded', () => {
    const {hub, pop} = reloadWithPopout();
    pop.runtime.dispatch({
      type: 'setTimeframe',
      chartId: 'chart-1',
      timeframe: '1h',
    });
    pop.runtime.dispatch({type: 'toggleVolume', chartId: 'chart-1'});
    expect(pop.link.pending.get()).toBe(2);
    expect(activeChart(pop.runtime.workspace.get()).timeframe).toBe('1h');
    const reloaded = mainWindow(hub);
    const chart = activeChart(reloaded.runtime.workspace.get());
    expect(chart.timeframe).toBe('1h');
    expect(chart.show_volume).toBe(false);
    // Acknowledged: nothing waits and nothing was applied twice.
    expect(pop.link.pending.get()).toBe(0);
    expect(activeChart(pop.runtime.workspace.get()).show_volume).toBe(false);
    reloaded.runtime.flush();
    const saved = JSON.parse(
      localStorage.getItem('joseph.workspace.v1.default') ?? '{}'
    ) as {charts?: Array<{timeframe: string}>};
    expect(saved.charts?.[0]?.timeframe).toBe('1h');
  });

  it('keeps the pop-out showing its changes until they are applied', () => {
    const {hub, pop} = reloadWithPopout();
    pop.runtime.dispatch({
      type: 'setChartType',
      chartId: 'chart-1',
      chartType: 'line',
    });
    // The new main window's first hello gets lost, and it broadcasts its
    // (older) workspace before hearing from the pop-out.
    const port = hub.port();
    let dropped = false;
    const lossy: ChannelPort = {
      postMessage(data) {
        const msg = (data as {msg?: PopoutMessage}).msg;
        if (!dropped && msg?.type === 'main-hello') {
          dropped = true;
          return;
        }
        port.postMessage(data);
      },
      onMessage: handler => port.onMessage(handler),
      close: () => void port.close(),
    };
    const reloaded = mainWindow(hub, {port: lossy});
    reloaded.runtime.dispatch({type: 'rename', name: 'Desk'});
    expect(pop.runtime.workspace.get().name).toBe('Desk');
    expect(activeChart(pop.runtime.workspace.get()).chart_type).toBe('line');
    expect(activeChart(reloaded.runtime.workspace.get()).chart_type).toBe(
      'candles'
    );
    // A later action carries the whole queue, oldest first.
    pop.runtime.dispatch({
      type: 'setPriceScale',
      chartId: 'chart-1',
      priceScale: 'log',
    });
    const chart = activeChart(reloaded.runtime.workspace.get());
    expect(chart.chart_type).toBe('line');
    expect(chart.price_scale).toBe('log');
    expect(pop.link.pending.get()).toBe(0);
  });

  it('applies an action once even when it arrives twice', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    const raw = new PopoutBus(hub.port(), 'default', 'popout-raw');
    const acks: unknown[] = [];
    raw.on(msg => {
      if (msg.type === 'workspace') acks.push(msg.applied?.['popout-raw']);
    });
    const toggle = {type: 'toggleVolume' as const, chartId: 'chart-1'};
    const pending = [{seq: 1, action: toggle}];
    raw.send({type: 'action', action: toggle, pending});
    raw.send({type: 'action', action: toggle, pending});
    expect(activeChart(main.runtime.workspace.get()).show_volume).toBe(false);
    expect(acks).toEqual([1, 1]);
    // Actions from older pop-outs without a queue still apply.
    raw.send({type: 'action', action: toggle});
    expect(activeChart(main.runtime.workspace.get()).show_volume).toBe(true);
    raw.close();
  });

  it('marks the main window lost when it does not come back', () => {
    const {pop} = reloadWithPopout();
    advance(16_000);
    expect(pop.link.main.get()).toBe('lost');
  });

  it('survives StrictMode re-running the effect on one runtime', async () => {
    const hub = new MemoryChannelHub();
    const first = mainWindow(hub);
    first.actions.detach(CHART_1, 'BTCUSD 5m');
    const pop = popoutWindow(hub, CHART_1);
    first.controller.dispose();
    const again = mainWindow(hub, {runtime: first.runtime});
    expect(statuses(again.runtime)).toEqual(['live']);
    await again.runtime.commands.execute(RESTORE_POPOUTS_COMMAND);
    expect(again.opened).toEqual([]);
    pop.runtime.dispatch({
      type: 'setTimeframe',
      chartId: 'chart-1',
      timeframe: '1d',
    });
    expect(activeChart(again.runtime.workspace.get()).timeframe).toBe('1d');
  });
});

describe('after a reload', () => {
  it('lets windows that survived the reload claim their panes', () => {
    const hub = new MemoryChannelHub();
    const first = mainWindow(hub);
    first.actions.detach(CHART_1, 'BTCUSD 5m');
    popoutWindow(hub, CHART_1);
    first.runtime.flush();
    first.controller.dispose();
    const reloaded = mainWindow(hub);
    const state = reloaded.runtime.detached.get();
    expect(detachedStatus(state.live['chart:chart-1'], clock)).toBe('live');
  });

  it('explains blocked windows', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub, {blocked: true});
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    expect(main.runtime.detached.get().panes).toEqual([]);
    expect(main.runtime.toasts.get().at(-1)?.message).toMatch(/blocked/);
  });

  it('keeps its own record even if an import overwrites the layout', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    main.runtime.dispatch({
      type: 'setPanel',
      featureId: DETACHED_PANEL_KEY,
      state: {panes: []},
    });
    expect(readDetachedPanes(main.runtime.workspace.get().panels)).toHaveLength(
      1
    );
  });
});

describe('trading guards in a pop-out', () => {
  it('starts at Paper, never copies the order mode and sees its own replay', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.runtime.orderMode.set('LIVE');
    main.actions.detach(CHART_1, 'BTCUSD 5m');
    const pop = popoutWindow(hub, CHART_1);
    main.runtime.dispatch({type: 'setSync', key: 'viewport', value: true});
    expect(pop.runtime.orderMode.get()).toBe('PAPER');
    expect(pop.runtime.selection.get().orderMode).toBe('PAPER');
    // Replay on the pop-out's chart turns trading off there (selection.replay).
    pop.runtime.replay.set({
      'chart-1': {phase: 'paused', cursorMs: 1, speed: 1},
    });
    expect(pop.runtime.selection.get().replay).toBe(true);
    expect(pop.runtime.selection.get().chartId).toBe('chart-1');
  });
});
