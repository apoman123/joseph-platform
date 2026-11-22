/**
 * @fileoverview Browser wiring of detachable panes.
 *
 * `startMainDetach` runs in the main window: it opens pop-out windows,
 * tracks their liveness (hello, heartbeats, `pagehide` goodbye and the
 * window handle's `closed` flag), re-docks crashed or closed windows,
 * remembers detached panes in the workspace layout and broadcasts the
 * workspace, theme and crosshair. `startPopoutLink` runs in a pop-out: it
 * adopts the main window's workspace and theme and forwards its own
 * workspace actions.
 *
 * Order modes, accounts and trading state never cross windows: each window
 * runs its own provider connection and the same trading guards.
 */
import {
  DETACHED_PANEL_KEY,
  HEARTBEAT_MS,
  PopoutBus,
  createTrailingThrottle,
  detachReducer,
  detachedPanelState,
  detachedStatus,
  lostKeys,
  paneKey,
  popoutUrl,
  readDetachedPanes,
  samePanes,
  type ChannelPort,
  type DetachEvent,
  type DetachedPane,
  type DetachedPaneRef,
  type PopoutMessage,
  type WorkspaceAction,
} from '@joseph/terminal-shell';
import {createStore, type ReadableStore} from '@joseph/feature-registry';
import type {SyncedPoint, TerminalRuntime, WindowActions} from './runtime';

/** Crosshair messages per second at most. */
const CROSSHAIR_INTERVAL_MS = 40;

/** Liveness check interval. */
const CHECK_MS = 1_000;

/** Time a just-docked pane ignores late heartbeats from its window. */
const DOCKED_QUIET_MS = 3_000;

/** Silence after which a pop-out reports the main window as gone. */
const MAIN_TIMEOUT_MS = 75_000;

/** Time a new pop-out waits for the main window's first answer. */
const MAIN_FIRST_CONTACT_MS = 6_000;

/** Browser services used by the controllers (injectable for tests). */
export interface DetachEnvironment {
  /** Channel port, or null when the browser has no BroadcastChannel. */
  port: ChannelPort | null;
  /**
   * Opens a window.
   * @param url Same-origin URL.
   * @param name Window name (re-used windows keep their name).
   * @param features `window.open` features.
   * @return Window handle or null when blocked.
   */
  open: (url: string, name: string, features: string) => Window | null;
  /**
   * Clock.
   * @return UTC ms.
   */
  now: () => number;
  /** This window's id. */
  windowId: string;
}

/** Running controller. */
export interface DetachController {
  /** Window actions exposed to the UI. */
  actions: WindowActions;
  /** Stops timers and closes the channel. */
  dispose(): void;
}

/**
 * Window name of a pane's pop-out (so re-opening reuses the window).
 * @param key Pane key.
 * @return Name.
 */
export function popoutWindowName(key: string): string {
  return `joseph-popout-${key.replace(/[^A-Za-z0-9_-]/g, '_')}`;
}

/**
 * Window features for a pane.
 * @param ref Pane reference.
 * @return `window.open` features string.
 */
export function popoutFeatures(ref: DetachedPaneRef): string {
  return ref.kind === 'chart'
    ? 'popup=yes,width=1100,height=720'
    : 'popup=yes,width=520,height=720';
}

/**
 * Starts the main window's detach controller.
 * @param runtime Main runtime.
 * @param env Browser services.
 * @return Controller.
 */
export function startMainDetach(
  runtime: TerminalRuntime,
  env: DetachEnvironment
): DetachController {
  const handles = new Map<string, Window>();
  /** When each pane was docked here (late heartbeats must not re-detach). */
  const docked = new Map<string, number>();
  const bus = env.port
    ? new PopoutBus(
        env.port,
        runtime.workspace.get().workspace_id,
        env.windowId
      )
    : null;
  const apply = (event: DetachEvent) =>
    runtime.detached.update(state => detachReducer(state, event));
  const send = (msg: PopoutMessage) => bus?.send(msg);
  const titleOf = (key: string) =>
    runtime.detached.get().panes.find(p => p.key === key)?.title ?? 'pane';

  // Restore what the saved layout remembers: dormant until a window claims it.
  apply({
    type: 'restore',
    panes: readDetachedPanes(runtime.workspace.get().panels),
  });

  /**
   * Opens (or re-opens) a pane's window.
   * @param pane Pane.
   * @return Whether the browser opened a window.
   */
  const openWindow = (pane: DetachedPane): boolean => {
    const url = popoutUrl(pane.ref, runtime.workspace.get().workspace_id);
    let handle: Window | null = null;
    try {
      handle = env.open(
        url,
        popoutWindowName(pane.key),
        popoutFeatures(pane.ref)
      );
    } catch {
      handle = null;
    }
    if (!handle) return false;
    handles.set(pane.key, handle);
    docked.delete(pane.key);
    apply({type: 'open', pane, nowMs: env.now()});
    return true;
  };

  const redockLocally = (key: string, message: string | null) => {
    if (!runtime.detached.get().panes.some(p => p.key === key)) return;
    handles.delete(key);
    docked.set(key, env.now());
    apply({type: 'close', key});
    if (message) runtime.notifier.notify(message, 'info');
  };

  const actions: WindowActions = {
    supported: bus !== null,
    detach(ref, title) {
      if (!bus) {
        runtime.notifier.notify(
          'This browser cannot link windows, so panes stay in this window.',
          'warning'
        );
        return;
      }
      const pane: DetachedPane = {key: paneKey(ref), ref, title};
      if (!openWindow(pane)) {
        runtime.notifier.notify(
          'The browser blocked the new window. Allow pop-ups for this site, then try again.',
          'warning'
        );
      }
    },
    redock(key) {
      send({type: 'redock', key});
      try {
        handles.get(key)?.close();
      } catch {
        // Cross-window access can fail while the window is unloading.
      }
      redockLocally(key, null);
    },
    focus(key) {
      const handle = handles.get(key);
      if (handle && !handle.closed) handle.focus();
      // Without a handle (after a reload) the pop-out focuses itself.
      send({type: 'focus', key});
    },
    reopen(key) {
      const pane = runtime.detached.get().panes.find(p => p.key === key);
      if (pane && !openWindow(pane)) {
        runtime.notifier.notify(
          `The browser blocked the window for ${pane.title}. Allow pop-ups for this site, then try again.`,
          'warning'
        );
      }
    },
    reopenAll() {
      const now = env.now();
      const state = runtime.detached.get();
      const dormant = state.panes.filter(
        p => detachedStatus(state.live[p.key], now) === 'dormant'
      );
      let opened = 0;
      for (const pane of dormant) if (openWindow(pane)) opened += 1;
      if (opened < dormant.length) {
        runtime.notifier.notify(
          opened === 0
            ? 'The browser blocked the windows. Allow pop-ups for this site, then try again.'
            : `Opened ${opened} of ${dormant.length} windows. The browser allows one window per click; click again for the rest.`,
          'warning'
        );
      }
    },
  };

  const disposers: (() => void)[] = [];
  // Crosshair: forward local moves, relay remote ones without echoing.
  let relaying = false;
  const relayCrosshair = (value: SyncedPoint) => {
    relaying = true;
    try {
      runtime.crosshair.emit(value);
    } finally {
      relaying = false;
    }
  };
  const announce = () => {
    send({type: 'workspace', doc: runtime.workspace.get()});
    send({type: 'theme', theme: runtime.theme.get()});
  };
  if (bus) {
    disposers.push(
      bus.on(msg => {
        switch (msg.type) {
          case 'popout-hello':
          case 'heartbeat':
            if (
              env.now() - (docked.get(msg.key) ?? -Infinity) <
              DOCKED_QUIET_MS
            )
              break;
            apply({
              type: 'seen',
              key: msg.key,
              ref: msg.ref,
              title: msg.title,
              hidden: msg.hidden,
              nowMs: env.now(),
            });
            if (msg.type === 'popout-hello') announce();
            break;
          case 'closed':
            redockLocally(
              msg.key,
              `${titleOf(msg.key)} is back in this window.`
            );
            break;
          case 'action':
            runtime.dispatch(msg.action);
            break;
          case 'theme':
            runtime.theme.set(msg.theme);
            break;
          case 'crosshair':
            relayCrosshair(msg.crosshair);
            break;
          default:
            break;
        }
      })
    );
  }

  const crosshair = createTrailingThrottle(
    (value: SyncedPoint) => send({type: 'crosshair', crosshair: value}),
    CROSSHAIR_INTERVAL_MS,
    env.now
  );
  disposers.push(() => crosshair.dispose());
  disposers.push(
    runtime.crosshair.subscribe(value => {
      if (!relaying && runtime.detached.get().panes.length > 0)
        crosshair.push(value);
    })
  );

  // Keep pop-outs in step and the layout's memory of detached panes current.
  disposers.push(
    runtime.workspace.subscribe(doc => {
      const panes = runtime.detached.get().panes;
      if (!samePanes(readDetachedPanes(doc.panels), panes)) {
        runtime.dispatch({
          type: 'setPanel',
          featureId: DETACHED_PANEL_KEY,
          state: detachedPanelState(panes),
        });
        return;
      }
      send({type: 'workspace', doc});
    }),
    runtime.detached.subscribe(state => {
      const doc = runtime.workspace.get();
      if (!samePanes(readDetachedPanes(doc.panels), state.panes)) {
        runtime.dispatch({
          type: 'setPanel',
          featureId: DETACHED_PANEL_KEY,
          state: detachedPanelState(state.panes),
        });
      }
    }),
    runtime.theme.subscribe(theme => send({type: 'theme', theme}))
  );

  // Liveness: closed handles and silent windows are re-docked.
  const timer = setInterval(() => {
    const now = env.now();
    for (const [key, handle] of [...handles]) {
      let closed = false;
      try {
        closed = handle.closed;
      } catch {
        closed = false;
      }
      if (closed) redockLocally(key, `${titleOf(key)} is back in this window.`);
    }
    for (const key of lostKeys(runtime.detached.get(), now)) {
      redockLocally(
        key,
        `The window for ${titleOf(key)} stopped responding, so the pane is back here.`
      );
    }
  }, CHECK_MS);
  const beat = setInterval(() => send({type: 'main-heartbeat'}), HEARTBEAT_MS);
  disposers.push(
    () => clearInterval(timer),
    () => clearInterval(beat)
  );
  // Ask surviving pop-outs (e.g. after this window reloaded) to say hello.
  send({type: 'main-hello'});

  return {
    actions,
    dispose() {
      for (const dispose of disposers.splice(0).reverse()) dispose();
      bus?.close();
    },
  };
}

/** Main-window connection seen from a pop-out. */
export type MainLinkState = 'connecting' | 'linked' | 'lost';

/** Running pop-out link. */
export interface PopoutLink {
  /** Whether the main window is reachable. */
  main: ReadableStore<MainLinkState>;
  /**
   * Forwards a workspace action to the main window.
   * @param action Action applied locally.
   */
  forward(action: WorkspaceAction): void;
  /** Tells the main window the pane goes back, then closes this window. */
  bringBack(): void;
  /** Tells the main window this window is going away (`pagehide`). */
  goodbye(): void;
  /** Stops timers and closes the channel. */
  dispose(): void;
}

/** Pop-out options. */
export interface PopoutLinkOptions {
  /** Pane this window shows. */
  ref: DetachedPaneRef;
  /** Human title. */
  title: () => string;
  /** Whether the document is hidden. */
  hidden: () => boolean;
  /** Closes this window. */
  close: () => void;
  /** Focuses this window. */
  focus: () => void;
}

/**
 * Starts a pop-out window's link to the main window.
 * @param runtime Pop-out runtime.
 * @param env Browser services.
 * @param options Pane and window callbacks.
 * @return Link.
 */
export function startPopoutLink(
  runtime: TerminalRuntime,
  env: DetachEnvironment,
  options: PopoutLinkOptions
): PopoutLink {
  const key = paneKey(options.ref);
  const main = createStore<MainLinkState>('connecting');
  const bus = env.port
    ? new PopoutBus(
        env.port,
        runtime.workspace.get().workspace_id,
        env.windowId
      )
    : null;
  const send = (msg: PopoutMessage) => bus?.send(msg);
  const startedMs = env.now();
  let mainSeen: number | null = null;
  const hello = () =>
    send({
      type: 'popout-hello',
      key,
      ref: options.ref,
      title: options.title(),
      hidden: options.hidden(),
    });
  const heartbeat = () =>
    send({
      type: 'heartbeat',
      key,
      ref: options.ref,
      title: options.title(),
      hidden: options.hidden(),
    });
  let relaying = false;
  const disposers: (() => void)[] = [];
  if (bus) {
    disposers.push(
      bus.on(msg => {
        switch (msg.type) {
          case 'main-hello':
            mainSeen = env.now();
            main.set('linked');
            hello();
            break;
          case 'main-heartbeat':
            mainSeen = env.now();
            main.set('linked');
            break;
          case 'workspace':
            mainSeen = env.now();
            main.set('linked');
            runtime.adoptWorkspace(msg.doc);
            break;
          case 'theme':
            runtime.theme.set(msg.theme);
            break;
          case 'crosshair':
            relaying = true;
            try {
              runtime.crosshair.emit(msg.crosshair);
            } finally {
              relaying = false;
            }
            break;
          case 'focus':
            if (msg.key === key) options.focus();
            break;
          case 'redock':
            if (msg.key === key) options.close();
            break;
          default:
            break;
        }
      })
    );
  }
  const crosshair = createTrailingThrottle(
    (value: SyncedPoint) => send({type: 'crosshair', crosshair: value}),
    CROSSHAIR_INTERVAL_MS,
    env.now
  );
  disposers.push(
    () => crosshair.dispose(),
    runtime.crosshair.subscribe(value => {
      if (!relaying) crosshair.push(value);
    }),
    runtime.theme.subscribe(theme => send({type: 'theme', theme}))
  );
  const beat = setInterval(() => {
    heartbeat();
    // The main window beats every 2 s; hidden windows are throttled, so the
    // budget is generous. A pop-out keeps working on its own meanwhile.
    const now = env.now();
    const silent =
      mainSeen === null
        ? now - startedMs > MAIN_FIRST_CONTACT_MS
        : now - mainSeen > MAIN_TIMEOUT_MS;
    if (silent) main.set('lost');
  }, HEARTBEAT_MS);
  disposers.push(() => clearInterval(beat));
  hello();
  return {
    main,
    forward(action) {
      send({type: 'action', action});
    },
    bringBack() {
      send({type: 'closed', key});
      options.close();
    },
    goodbye() {
      send({type: 'closed', key});
    },
    dispose() {
      for (const dispose of disposers.splice(0).reverse()) dispose();
      bus?.close();
    },
  };
}
