/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser wiring of detachable panes.
 *
 * `startMainDetach` runs in the main window: it opens pop-out windows,
 * tracks their liveness (hello, heartbeats, `pagehide` goodbye and the
 * window handle's `closed` flag), re-docks crashed or closed windows,
 * remembers detached panes (and their window position) in the workspace
 * layout and broadcasts the workspace, theme and crosshair. After a reload
 * it probes for pop-outs that kept running and re-adopts them without a
 * click; only panes nobody claims are offered for "Restore pop-outs".
 * `startPopoutLink` runs in a pop-out: it adopts the main window's
 * workspace and theme and forwards its own workspace actions, keeping them
 * until the main window acknowledges them so a reloading main window
 * cannot lose them.
 *
 * Order modes, accounts and trading state never cross windows: each window
 * runs its own provider connection and the same trading guards.
 */

import {
  DETACHED_PANEL_KEY,
  HEARTBEAT_MS,
  PROBE_MS,
  PopoutBus,
  createTrailingThrottle,
  detachReducer,
  detachedPanelState,
  dormantPanes,
  lostKeys,
  paneKey,
  parseGeometry,
  parsePending,
  popoutUrl,
  queueAction,
  readDetachedPanes,
  rebaseWorkspace,
  samePanes,
  unappliedActions,
  type ChannelPort,
  type DetachEvent,
  type DetachedPane,
  type DetachedPaneRef,
  type PopoutMessage,
  type SequencedAction,
  type WindowGeometry,
  type WorkspaceAction,
} from '@joseph/terminal-shell';
import {createStore, type ReadableStore} from '@joseph/feature-registry';
import type {SyncedPoint, TerminalRuntime, WindowActions} from './runtime';

/** Minimum gap between crosshair messages (at most 25 per second). */
const CROSSHAIR_INTERVAL_MS = 40;

/** Interval of the main window's liveness check of its pop-outs. */
const CHECK_MS = 1_000;

/** Time a just-docked pane ignores late heartbeats from its window. */
const DOCKED_QUIET_MS = 3_000;

/** Silence after which a pop-out reports the main window as gone. */
const MAIN_TIMEOUT_MS = 75_000;

/** Time a new pop-out waits for the main window's first answer. */
const MAIN_FIRST_CONTACT_MS = 6_000;

/** Time a pop-out waits for a reloading main window to come back. */
const MAIN_RETURN_MS = 15_000;

/** Command that restores pop-outs remembered by the layout. */
export const RESTORE_POPOUTS_COMMAND = 'windows.restore';

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
   * @return Current time, UTC ms.
   */
  now: () => number;
  /** This window's id, unique per page load. */
  windowId: string;
}

/** Running main-window controller. */
export interface DetachController {
  /** Window actions exposed to the UI. */
  actions: WindowActions;
  /** Tells pop-outs this window is going away (`pagehide`, e.g. reload). */
  goodbye(): void;
  /** Stops timers and closes the channel. */
  dispose(): void;
}

/**
 * Returns the window name of a pane's pop-out, so re-opening reuses the
 * window.
 * @param key Pane key.
 * @return Name made of letters, digits, `_` and `-` only.
 */
export function popoutWindowName(key: string): string {
  return `joseph-popout-${key.replace(/[^A-Za-z0-9_-]/g, '_')}`;
}

/**
 * Returns the window features for a pane, at its remembered position when
 * known.
 * @param ref Pane reference; charts get a larger default window than panels.
 * @param geometry Remembered window position and size, in screen pixels.
 * @return `window.open` features string.
 */
export function popoutFeatures(
  ref: DetachedPaneRef,
  geometry?: WindowGeometry | null
): string {
  if (geometry) {
    const {left, top, width, height} = geometry;
    return `popup=yes,width=${width},height=${height},left=${left},top=${top}`;
  }
  return ref.kind === 'chart'
    ? 'popup=yes,width=1100,height=720'
    : 'popup=yes,width=520,height=720';
}

/**
 * Explains how many remembered windows a restore click opened.
 * @param opened Number of windows the browser opened.
 * @param wanted Number of windows to restore.
 * @return Message, or null when every window opened.
 */
export function restoreMessage(opened: number, wanted: number): string | null {
  if (opened >= wanted) return null;
  if (opened === 0) {
    return 'The browser blocked the windows. Allow pop-ups for this site (the blocked-pop-up icon in the address bar), then choose Restore pop-outs again; with pop-ups allowed one click restores every window.';
  }
  return `Restored ${opened} of ${wanted} windows. Without pop-up permission the browser opens one window per click: choose Restore pop-outs again for the next one, or allow pop-ups for this site to restore the rest at once.`;
}

/**
 * Starts the main window's detach controller. Without a channel port
 * (`env.port` null) panes cannot be detached and `actions.supported` is
 * false.
 * @param runtime Main runtime.
 * @param env Browser services.
 * @return Controller; call `dispose` to stop it.
 */
export function startMainDetach(
  runtime: TerminalRuntime,
  env: DetachEnvironment
): DetachController {
  const handles = new Map<string, Window>();
  // When each pane was docked here (late heartbeats must not re-detach).
  const docked = new Map<string, number>();
  // Sequence number of the last pop-out action applied, per pop-out window.
  const applied = new Map<string, number>();
  const bus = env.port
    ? new PopoutBus(
        env.port,
        runtime.workspace.get().workspace_id,
        env.windowId
      )
    : null;
  const apply = (event: DetachEvent) =>
    void runtime.detached.update(state => detachReducer(state, event));
  const send = (msg: PopoutMessage) => bus?.send(msg);
  const titleOf = (key: string) =>
    runtime.detached.get().panes.find(p => p.key === key)?.title ?? 'pane';
  const disposers: Array<() => void> = [];

  // Restore what the saved layout remembers. Pop-outs that survived a reload
  // of this window claim their panes during the probe; the rest turn dormant.
  const remembered = readDetachedPanes(runtime.workspace.get().panels);
  apply({type: 'restore', panes: remembered});
  if (remembered.length > 0 && bus) {
    apply({type: 'probe', probing: true});
    const probe = setTimeout(
      () => void apply({type: 'probe', probing: false}),
      PROBE_MS
    );
    disposers.push(() => void clearTimeout(probe));
  }

  /**
   * Opens (or re-opens) a pane's window.
   * @param pane Pane to show in the window.
   * @return Whether the browser opened a window (false when blocked).
   */
  const openWindow = (pane: DetachedPane): boolean => {
    const url = popoutUrl(pane.ref, runtime.workspace.get().workspace_id);
    let handle: Window | null;
    try {
      handle = env.open(
        url,
        popoutWindowName(pane.key),
        popoutFeatures(pane.ref, pane.geometry)
      );
    } catch {
      // `open` may throw instead of returning null; treat it as blocked.
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

  const acknowledgements = (): Record<string, number> =>
    Object.fromEntries(applied);
  const sendWorkspace = () =>
    send({
      type: 'workspace',
      doc: runtime.workspace.get(),
      applied: acknowledgements(),
    });

  /**
   * Applies a pop-out's unacknowledged actions in order, then makes sure
   * the pop-out hears the acknowledgement.
   * @param from Pop-out window id.
   * @param raw Untrusted pending actions.
   */
  const replay = (from: string, raw: unknown) => {
    const pending = parsePending(raw) ?? [];
    if (pending.length === 0) return;
    const todo = unappliedActions(pending, applied.get(from) ?? 0);
    const last = todo.at(-1);
    if (!last) {
      // Already applied: the acknowledgement was lost, so repeat it.
      sendWorkspace();
      return;
    }
    // Acknowledge first, so the workspace broadcasts below carry it.
    applied.set(from, last.seq);
    const before = runtime.workspace.get();
    for (const entry of todo) {
      runtime.dispatch(entry.action);
    }
    if (runtime.workspace.get() === before) sendWorkspace();
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
      const state = runtime.detached.get();
      if (state.probing) {
        runtime.notifier.notify(
          'Still looking for windows that kept running during the reload. Try again in a moment.',
          'info'
        );
        return;
      }
      const dormant = dormantPanes(state, env.now());
      if (dormant.length === 0) {
        runtime.notifier.notify(
          'Every detached pane already has its window.',
          'info'
        );
        return;
      }
      let opened = 0;
      // Browsers allow one window per click unless pop-ups are allowed for
      // the site; try them all so an allowed site restores everything.
      for (const pane of dormant) {
        if (openWindow(pane)) opened += 1;
      }
      const message = restoreMessage(opened, dormant.length);
      if (message) runtime.notifier.notify(message, 'warning');
    },
  };

  try {
    disposers.push(
      runtime.commands.register({
        id: RESTORE_POPOUTS_COMMAND,
        title: 'Restore pop-outs',
        run: () => void actions.reopenAll(),
      })
    );
  } catch {
    // Another controller of this runtime already offers the command.
  }

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
    sendWorkspace();
    send({type: 'theme', theme: runtime.theme.get()});
  };
  if (bus) {
    disposers.push(
      bus.on((msg, from) => {
        switch (msg.type) {
          case 'popout-hello':
          case 'heartbeat':
            if (
              env.now() - (docked.get(msg.key) ?? -Infinity) <
              DOCKED_QUIET_MS
            ) {
              break;
            }
            apply({
              type: 'seen',
              key: msg.key,
              ref: msg.ref,
              title: msg.title,
              hidden: msg.hidden,
              nowMs: env.now(),
              geometry: parseGeometry(msg.geometry),
            });
            // Changes made in the pop-out while this window was away.
            replay(from, msg.pending);
            if (msg.type === 'popout-hello') announce();
            break;
          case 'closed':
            redockLocally(
              msg.key,
              `${titleOf(msg.key)} is back in this window.`
            );
            break;
          case 'action':
            if (msg.pending !== undefined) replay(from, msg.pending);
            else runtime.dispatch(msg.action);
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
  disposers.push(() => void crosshair.dispose());
  disposers.push(
    runtime.crosshair.subscribe(value => {
      if (!relaying && runtime.detached.get().panes.length > 0) {
        crosshair.push(value);
      }
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
      send({type: 'workspace', doc, applied: acknowledgements()});
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
      let closed: boolean;
      try {
        closed = handle.closed;
      } catch {
        // Cross-window access can fail while the window is unloading; the
        // heartbeat timeout catches a window that really went away.
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
    () => void clearInterval(timer),
    () => void clearInterval(beat)
  );
  // Ask surviving pop-outs (e.g. after this window reloaded) to say hello.
  send({type: 'main-hello'});

  return {
    actions,
    goodbye() {
      send({type: 'main-bye'});
    },
    dispose() {
      for (const dispose of disposers.splice(0).reverse()) {
        dispose();
      }
      bus?.close();
    },
  };
}

/**
 * Main-window connection seen from a pop-out: `reconnecting` after the main
 * window said goodbye (usually a reload) until it says hello again.
 */
export type MainLinkState = 'connecting' | 'linked' | 'reconnecting' | 'lost';

/** Running pop-out link. */
export interface PopoutLink {
  /** Connection state to the main window. */
  main: ReadableStore<MainLinkState>;
  /** Number of actions the main window has not acknowledged yet. */
  pending: ReadableStore<number>;
  /**
   * Forwards a workspace action to the main window (kept until the main
   * window acknowledges it).
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
  /** Returns the pane's current title (it follows symbol changes). */
  title: () => string;
  /**
   * Returns whether the document is hidden. Browsers throttle hidden
   * windows' timers, so the main window allows them a longer silence.
   */
  hidden: () => boolean;
  /** Closes this window. */
  close: () => void;
  /** Focuses this window. */
  focus: () => void;
  /**
   * Window position and size reported to the main window (remembered for
   * "Restore pop-outs").
   * @return Geometry or null when unknown.
   */
  geometry?: () => WindowGeometry | null;
}

/**
 * Starts a pop-out window's link to the main window. Without a channel port
 * the link never connects and ends up `lost`; the window keeps working on
 * its own.
 * @param runtime Pop-out runtime.
 * @param env Browser services.
 * @param options Pane and window callbacks.
 * @return Link; call `dispose` to stop it.
 */
export function startPopoutLink(
  runtime: TerminalRuntime,
  env: DetachEnvironment,
  options: PopoutLinkOptions
): PopoutLink {
  const key = paneKey(options.ref);
  const main = createStore<MainLinkState>('connecting');
  const pendingCount = createStore(0);
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
  let byeMs: number | null = null;
  let seq = 0;
  let pending: SequencedAction[] = [];
  const setPending = (next: SequencedAction[]) => {
    pending = next;
    pendingCount.set(pending.length);
  };
  const heard = () => {
    mainSeen = env.now();
    byeMs = null;
    main.set('linked');
  };
  const report = () => ({
    key,
    ref: options.ref,
    title: options.title(),
    hidden: options.hidden(),
    geometry: parseGeometry(options.geometry?.() ?? null),
    ...(pending.length > 0 ? {pending: [...pending]} : {}),
  });
  const hello = () => send({type: 'popout-hello', ...report()});
  const heartbeat = () => send({type: 'heartbeat', ...report()});
  let relaying = false;
  const disposers: Array<() => void> = [];
  if (bus) {
    disposers.push(
      bus.on(msg => {
        switch (msg.type) {
          case 'main-hello':
            heard();
            // Includes the actions a reloading main window missed.
            hello();
            break;
          case 'main-heartbeat':
            heard();
            break;
          case 'main-bye':
            byeMs = env.now();
            main.set('reconnecting');
            break;
          case 'workspace': {
            heard();
            const acknowledged = msg.applied?.[env.windowId] ?? 0;
            setPending(unappliedActions(pending, acknowledged));
            // Keep showing changes the main window has not applied yet.
            runtime.adoptWorkspace(
              rebaseWorkspace(msg.doc, pending, env.now())
            );
            break;
          }
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
    () => void crosshair.dispose(),
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
      byeMs !== null
        ? now - byeMs > MAIN_RETURN_MS
        : mainSeen === null
          ? now - startedMs > MAIN_FIRST_CONTACT_MS
          : now - mainSeen > MAIN_TIMEOUT_MS;
    if (silent) main.set('lost');
  }, HEARTBEAT_MS);
  disposers.push(() => void clearInterval(beat));
  hello();
  return {
    main,
    pending: pendingCount,
    forward(action) {
      seq += 1;
      setPending(queueAction(pending, {seq, action}));
      send({type: 'action', action, pending: [...pending]});
    },
    bringBack() {
      send({type: 'closed', key});
      options.close();
    },
    goodbye() {
      send({type: 'closed', key});
    },
    dispose() {
      for (const dispose of disposers.splice(0).reverse()) {
        dispose();
      }
      bus?.close();
    },
  };
}
