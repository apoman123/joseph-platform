/**
 * @fileoverview Main window and pop-out windows over an in-memory channel:
 * detaching, workspace/link-group/theme/crosshair sync, action forwarding,
 * re-docking on goodbye, closed handles and crashed windows, remembered
 * panes after a reload, and trading guards that never cross windows.
 */
import {
  DETACHED_PANEL_KEY,
  LIVE_TIMEOUT_MS,
  MemoryChannelHub,
  activeChart,
  detachedStatus,
  readDetachedPanes,
  type DetachedPaneRef,
} from '@joseph/terminal-shell';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {
  popoutWindowName,
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
const cleanups: (() => void)[] = [];

beforeEach(() => {
  vi.useFakeTimers();
  clock = 1_800_000_000_000;
  localStorage.clear();
});

afterEach(() => {
  for (const cleanup of cleanups.splice(0).reverse()) cleanup();
  vi.useRealTimers();
});

/**
 * Advances fake time and the injected clock together.
 * @param ms Milliseconds.
 */
function advance(ms: number): void {
  clock += ms;
  vi.advanceTimersByTime(ms);
}

/**
 * Starts a main window.
 * @param hub Channel hub.
 * @param blocked Whether `window.open` is blocked.
 * @return Runtime, controller, opened windows and their URLs.
 */
function mainWindow(hub: MemoryChannelHub, blocked = false) {
  const runtime = createRuntime(() => [], DEMO);
  const opened: {url: string; name: string; handle: FakeWindow}[] = [];
  const controller: DetachController = startMainDetach(runtime, {
    port: hub.port(),
    open: (url, name) => {
      if (blocked) return null;
      const handle: FakeWindow = {
        closed: false,
        focus: vi.fn(),
        close: vi.fn(),
      };
      opened.push({url, name, handle});
      return handle as unknown as Window;
    },
    now: () => clock,
    windowId: `main-${Math.random()}`,
  });
  runtime.windows.set(controller.actions);
  cleanups.push(() => controller.dispose());
  return {runtime, controller, opened, actions: controller.actions};
}

/**
 * Starts a pop-out window for a pane.
 * @param hub Channel hub.
 * @param ref Pane.
 * @return Runtime, link and window callbacks.
 */
function popoutWindow(hub: MemoryChannelHub, ref: DetachedPaneRef) {
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
    {ref, title: () => 'pane', hidden: () => false, close, focus}
  );
  const started = link;
  cleanups.push(() => started.dispose());
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
    ) as {charts?: {timeframe: string}[]};
    main.runtime.flush();
    const after = JSON.parse(
      localStorage.getItem('joseph.workspace.v1.default') ?? '{}'
    ) as {charts?: {timeframe: string}[]};
    expect(saved.charts?.[0]?.timeframe).not.toBe('1M');
    expect(after.charts?.[0]?.timeframe).toBe('1M');
  });

  it('follows link groups into the pop-out', () => {
    const hub = new MemoryChannelHub();
    const main = mainWindow(hub);
    main.runtime.dispatch({type: 'setLayout', layout: '2h'});
    main.runtime.dispatch({type: 'setSync', key: 'instrument', value: true});
    for (const chartId of ['chart-1', 'chart-2'])
      main.runtime.dispatch({type: 'setLinkGroup', chartId, group: 'A'});
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

describe('after a reload', () => {
  it('keeps remembered panes dormant until re-opened from a click', () => {
    const hub = new MemoryChannelHub();
    const first = mainWindow(hub);
    first.actions.detach(CHART_1, 'BTCUSD 5m');
    first.actions.detach(
      {kind: 'panel', slot: 'bottom-panel', panelId: 'alerts'},
      'Alerts'
    );
    first.runtime.flush();
    first.controller.dispose();
    const reloaded = mainWindow(new MemoryChannelHub());
    const state = reloaded.runtime.detached.get();
    expect(state.panes.map(p => p.key)).toEqual([
      'chart:chart-1',
      'panel:alerts',
    ]);
    expect(
      state.panes.map(p => detachedStatus(state.live[p.key], clock))
    ).toEqual(['dormant', 'dormant']);
    // Dormant panes are never re-docked by the liveness timer.
    advance(60_000);
    expect(reloaded.runtime.detached.get().panes).toHaveLength(2);
    reloaded.actions.reopenAll();
    expect(reloaded.opened.map(o => o.url)).toEqual([
      '/popout?pane=chart%3Achart-1&ws=default',
      '/popout?pane=panel%3Aalerts&ws=default&slot=bottom-panel',
    ]);
  });

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
    const main = mainWindow(hub, true);
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
