/**
 * @fileoverview Detachable panes (pure model and window protocol).
 *
 * A chart pane or a dock panel can be popped out into its own browser
 * window. The main window stays the single writer of the workspace: it
 * broadcasts the document, theme and crosshair over a same-origin channel
 * (`BroadcastChannel`), and pop-outs send workspace actions back. Pop-outs
 * run their own provider connection and their own trading guards; nothing
 * here carries order modes, accounts or credentials.
 *
 * Liveness: pop-outs say hello, send heartbeats (slower budgets while the
 * window is hidden, because browsers throttle background timers) and say
 * goodbye on `pagehide`. A pane seen in this session that goes silent is
 * re-docked (crashed window); a pane restored from the saved layout that
 * no window claims is "dormant" and offered for re-opening, because
 * browsers only open windows from a click.
 */
import type {WorkspaceDocument} from '@joseph/contracts';
import type {WorkspaceAction} from './workspace';

/** Workspace panel key holding the detached panes. */
export const DETACHED_PANEL_KEY = 'shell.detached';

/** Channel name shared by the main window and its pop-outs. */
export const POPOUT_CHANNEL = 'joseph.popout.v1';

/** Route of pop-out windows. */
export const POPOUT_PATH = '/popout';

/** Pop-out heartbeat interval. */
export const HEARTBEAT_MS = 2_000;

/** Silence after which a visible pop-out counts as crashed. */
export const LIVE_TIMEOUT_MS = 10_000;

/** Silence budget while the pop-out reported itself hidden (throttled). */
export const HIDDEN_TIMEOUT_MS = 90_000;

/** Time a newly opened window has to say hello. */
export const OPEN_GRACE_MS = 20_000;

/** Dock panels that may be popped out (contribution ids). */
export const DETACHABLE_PANELS: ReadonlySet<string> = new Set([
  'market-info',
  'watchlist',
  'screener',
  'alerts',
]);

/** What a pop-out window shows. */
export type DetachedPaneRef =
  | {kind: 'chart'; chartId: string}
  | {kind: 'panel'; slot: 'side-panel' | 'bottom-panel'; panelId: string};

/** A pane living in another window (persisted with the layout). */
export interface DetachedPane {
  /** Stable key, e.g. `chart:chart-2` or `panel:alerts`. */
  key: string;
  /** What the window shows. */
  ref: DetachedPaneRef;
  /** Human title such as `BTCUSD 5m` or `Alerts`. */
  title: string;
}

/** Runtime liveness of one detached pane (never persisted). */
export interface PaneLiveness {
  /** When this window opened it (this session), if it did. */
  openedAtMs?: number;
  /** Last hello or heartbeat. */
  seenMs?: number;
  /** Whether the pop-out last reported itself hidden. */
  hidden?: boolean;
}

/** Status of a detached pane as seen by the main window. */
export type DetachedStatus = 'opening' | 'live' | 'dormant' | 'lost';

/** Main-window state of detached panes. */
export interface DetachState {
  /** Detached panes in detach order. */
  panes: readonly DetachedPane[];
  /** Liveness by key. */
  live: Readonly<Record<string, PaneLiveness>>;
}

/** Empty state. */
export const EMPTY_DETACH_STATE: DetachState = {panes: [], live: {}};

/**
 * Stable key of a pane reference.
 * @param ref Pane reference.
 * @return Key such as `chart:chart-2` or `panel:alerts`.
 */
export function paneKey(ref: DetachedPaneRef): string {
  return ref.kind === 'chart' ? `chart:${ref.chartId}` : `panel:${ref.panelId}`;
}

/** Allowed id characters (chart and contribution ids). */
const ID_PATTERN = /^[A-Za-z0-9._-]{1,64}$/;

/**
 * Validates an untrusted pane reference.
 * @param value Candidate.
 * @return Reference or null.
 */
export function parsePaneRef(value: unknown): DetachedPaneRef | null {
  if (!value || typeof value !== 'object') return null;
  const ref = value as Record<string, unknown>;
  if (
    ref.kind === 'chart' &&
    typeof ref.chartId === 'string' &&
    ID_PATTERN.test(ref.chartId)
  ) {
    return {kind: 'chart', chartId: ref.chartId};
  }
  if (
    ref.kind === 'panel' &&
    (ref.slot === 'side-panel' || ref.slot === 'bottom-panel') &&
    typeof ref.panelId === 'string' &&
    DETACHABLE_PANELS.has(ref.panelId)
  ) {
    return {kind: 'panel', slot: ref.slot, panelId: ref.panelId};
  }
  return null;
}

/**
 * Reads persisted detached panes defensively from `workspace.panels`.
 * @param panels Workspace panel state.
 * @return Valid panes (duplicates dropped).
 */
export function readDetachedPanes(panels: unknown): DetachedPane[] {
  const raw = (panels as Record<string, unknown> | null | undefined)?.[
    DETACHED_PANEL_KEY
  ];
  const list = (raw as {panes?: unknown} | undefined)?.panes;
  if (!Array.isArray(list)) return [];
  const panes: DetachedPane[] = [];
  for (const item of list.slice(0, 16)) {
    const ref = parsePaneRef((item as {ref?: unknown} | null)?.ref);
    if (!ref) continue;
    const key = paneKey(ref);
    if (panes.some(p => p.key === key)) continue;
    const title = (item as {title?: unknown}).title;
    panes.push({
      key,
      ref,
      title: typeof title === 'string' ? title.slice(0, 80) : key,
    });
  }
  return panes;
}

/**
 * Panel state to persist for detached panes.
 * @param panes Detached panes.
 * @return Value for `panels['shell.detached']`.
 */
export function detachedPanelState(panes: readonly DetachedPane[]): {
  panes: DetachedPane[];
} {
  return {panes: panes.map(p => ({key: p.key, ref: p.ref, title: p.title}))};
}

/**
 * Whether two pane lists hold the same keys in the same order.
 * @param a First list.
 * @param b Second list.
 * @return True when equal.
 */
export function samePanes(
  a: readonly DetachedPane[],
  b: readonly DetachedPane[]
): boolean {
  return a.length === b.length && a.every((p, i) => p.key === b[i]?.key);
}

/**
 * Status of a pane right now.
 * @param live Liveness (undefined for a pane restored from the layout).
 * @param nowMs Clock.
 * @return `opening` (waiting for a new window), `live`, `dormant` (restored,
 *     unclaimed) or `lost` (went silent or never came up: re-dock).
 */
export function detachedStatus(
  live: PaneLiveness | undefined,
  nowMs: number
): DetachedStatus {
  if (live?.seenMs !== undefined) {
    const budget = live.hidden ? HIDDEN_TIMEOUT_MS : LIVE_TIMEOUT_MS;
    return nowMs - live.seenMs <= budget ? 'live' : 'lost';
  }
  if (live?.openedAtMs !== undefined) {
    return nowMs - live.openedAtMs <= OPEN_GRACE_MS ? 'opening' : 'lost';
  }
  return 'dormant';
}

/** Inputs of the detach reducer. */
export type DetachEvent =
  | {type: 'restore'; panes: readonly DetachedPane[]}
  | {type: 'open'; pane: DetachedPane; nowMs: number}
  | {
      type: 'seen';
      key: string;
      ref: DetachedPaneRef;
      title: string;
      hidden: boolean;
      nowMs: number;
    }
  | {type: 'close'; key: string}
  | {type: 'expire'; nowMs: number};

/**
 * Pure reducer of the main window's detached panes.
 * @param state Current state.
 * @param event Event.
 * @return Next state (same object when nothing changed).
 */
export function detachReducer(
  state: DetachState,
  event: DetachEvent
): DetachState {
  switch (event.type) {
    case 'restore': {
      const known = new Set(state.panes.map(p => p.key));
      const added = event.panes.filter(p => !known.has(p.key));
      return added.length === 0
        ? state
        : {...state, panes: [...state.panes, ...added]};
    }
    case 'open': {
      const exists = state.panes.some(p => p.key === event.pane.key);
      return {
        panes: exists ? state.panes : [...state.panes, event.pane],
        live: {
          ...state.live,
          [event.pane.key]: {
            ...state.live[event.pane.key],
            openedAtMs: event.nowMs,
          },
        },
      };
    }
    case 'seen': {
      // A surviving pop-out may claim a pane the main window forgot.
      const exists = state.panes.some(p => p.key === event.key);
      return {
        panes: exists
          ? state.panes
          : [
              ...state.panes,
              {key: event.key, ref: event.ref, title: event.title},
            ],
        live: {
          ...state.live,
          [event.key]: {
            ...state.live[event.key],
            seenMs: event.nowMs,
            hidden: event.hidden,
          },
        },
      };
    }
    case 'close': {
      if (!state.panes.some(p => p.key === event.key)) return state;
      const {[event.key]: _gone, ...live} = state.live;
      return {panes: state.panes.filter(p => p.key !== event.key), live};
    }
    case 'expire': {
      const lost = lostKeys(state, event.nowMs);
      if (lost.length === 0) return state;
      let next = state;
      for (const key of lost) next = detachReducer(next, {type: 'close', key});
      return next;
    }
    default:
      return state;
  }
}

/**
 * Keys of panes whose window went silent or never came up.
 * @param state State.
 * @param nowMs Clock.
 * @return Keys to re-dock.
 */
export function lostKeys(state: DetachState, nowMs: number): string[] {
  return state.panes
    .filter(p => detachedStatus(state.live[p.key], nowMs) === 'lost')
    .map(p => p.key);
}

/**
 * Builds the URL of a pop-out window.
 * @param ref Pane reference.
 * @param workspaceId Workspace id.
 * @return Same-origin path with query.
 */
export function popoutUrl(ref: DetachedPaneRef, workspaceId: string): string {
  const query = new URLSearchParams({pane: paneKey(ref), ws: workspaceId});
  if (ref.kind === 'panel') query.set('slot', ref.slot);
  return `${POPOUT_PATH}?${query.toString()}`;
}

/** Target of a pop-out window. */
export interface PopoutTarget {
  /** Pane shown. */
  ref: DetachedPaneRef;
  /** Workspace id. */
  workspaceId: string;
}

/**
 * Parses a pop-out window's query string.
 * @param search `location.search`.
 * @return Target, or null for an invalid link.
 */
export function parsePopoutQuery(search: string): PopoutTarget | null {
  const query = new URLSearchParams(search);
  const pane = query.get('pane') ?? '';
  const workspaceId = query.get('ws') ?? 'default';
  if (!ID_PATTERN.test(workspaceId)) return null;
  const [kind, id] = pane.split(':', 2);
  const ref = parsePaneRef(
    kind === 'chart'
      ? {kind, chartId: id}
      : {kind, panelId: id, slot: query.get('slot')}
  );
  return ref ? {ref, workspaceId} : null;
}

/** Crosshair point shared between windows. */
export interface SharedCrosshair {
  /** Chart the pointer is over. */
  sourceChartId: string;
  /** Time and price, or null when the pointer left. */
  point: {timeMs: number; price: number} | null;
}

/** Messages exchanged between the main window and pop-outs. */
export type PopoutMessage =
  | {type: 'main-hello'}
  | {type: 'main-heartbeat'}
  | {type: 'workspace'; doc: WorkspaceDocument}
  | {type: 'theme'; theme: 'dark' | 'light'}
  | {type: 'crosshair'; crosshair: SharedCrosshair}
  | {type: 'focus'; key: string}
  | {type: 'redock'; key: string}
  | {
      type: 'popout-hello';
      key: string;
      ref: DetachedPaneRef;
      title: string;
      hidden: boolean;
    }
  | {
      type: 'heartbeat';
      key: string;
      ref: DetachedPaneRef;
      title: string;
      hidden: boolean;
    }
  | {type: 'closed'; key: string}
  | {type: 'action'; action: WorkspaceAction};

/** Wire envelope. */
export interface PopoutEnvelope {
  /** Protocol version. */
  v: 1;
  /** Workspace the windows share. */
  workspaceId: string;
  /** Sender window id. */
  from: string;
  /** Message. */
  msg: PopoutMessage;
}

/** Message types accepted from the channel. */
const MESSAGE_TYPES: ReadonlySet<string> = new Set([
  'main-hello',
  'main-heartbeat',
  'workspace',
  'theme',
  'crosshair',
  'focus',
  'redock',
  'popout-hello',
  'heartbeat',
  'closed',
  'action',
]);

/**
 * Validates an untrusted channel message (same-origin, but still data).
 * @param value Received data.
 * @return Envelope or null.
 */
export function parseEnvelope(value: unknown): PopoutEnvelope | null {
  if (!value || typeof value !== 'object') return null;
  const env = value as Partial<PopoutEnvelope>;
  const msg = env.msg as Record<string, unknown> | undefined;
  if (
    env.v !== 1 ||
    typeof env.workspaceId !== 'string' ||
    typeof env.from !== 'string' ||
    !msg ||
    typeof msg.type !== 'string' ||
    !MESSAGE_TYPES.has(msg.type)
  ) {
    return null;
  }
  switch (msg.type) {
    case 'workspace':
      if (!msg.doc || typeof msg.doc !== 'object') return null;
      break;
    case 'theme':
      if (msg.theme !== 'dark' && msg.theme !== 'light') return null;
      break;
    case 'crosshair': {
      const crosshair = msg.crosshair as Partial<SharedCrosshair> | undefined;
      if (!crosshair || typeof crosshair.sourceChartId !== 'string')
        return null;
      break;
    }
    case 'focus':
    case 'redock':
    case 'closed':
      if (typeof msg.key !== 'string') return null;
      break;
    case 'popout-hello':
    case 'heartbeat': {
      const ref = parsePaneRef(msg.ref);
      if (!ref || msg.key !== paneKey(ref)) return null;
      break;
    }
    case 'action': {
      const action = msg.action as {type?: unknown} | undefined;
      if (!action || typeof action.type !== 'string') return null;
      break;
    }
    default:
      break;
  }
  return env as PopoutEnvelope;
}

/** Minimal message port (BroadcastChannel or an in-memory test hub). */
export interface ChannelPort {
  /**
   * Posts data to every other port on the channel.
   * @param data Structured-cloneable data.
   */
  postMessage(data: unknown): void;
  /**
   * Subscribes to data from other ports.
   * @param handler Handler.
   * @return Unsubscribe.
   */
  onMessage(handler: (data: unknown) => void): () => void;
  /** Closes the port. */
  close(): void;
}

/**
 * Opens a `BroadcastChannel` port, when the browser has one.
 * @param name Channel name.
 * @return Port, or null when unsupported.
 */
export function broadcastChannelPort(name: string): ChannelPort | null {
  if (typeof BroadcastChannel === 'undefined') return null;
  const channel = new BroadcastChannel(name);
  return {
    postMessage: data => channel.postMessage(data),
    onMessage: handler => {
      const listener = (event: MessageEvent) => handler(event.data);
      channel.addEventListener('message', listener);
      return () => channel.removeEventListener('message', listener);
    },
    close: () => channel.close(),
  };
}

/** In-memory channel for tests: delivers to every other port. */
export class MemoryChannelHub {
  private readonly ports = new Set<{
    handlers: Set<(data: unknown) => void>;
  }>();

  /**
   * Creates a port on the hub.
   * @return Port.
   */
  port(): ChannelPort {
    const self = {handlers: new Set<(data: unknown) => void>()};
    this.ports.add(self);
    return {
      postMessage: data => {
        const copy: unknown = structuredClone(data);
        for (const other of [...this.ports]) {
          if (other === self) continue;
          for (const handler of [...other.handlers]) handler(copy);
        }
      },
      onMessage: handler => {
        self.handlers.add(handler);
        return () => {
          self.handlers.delete(handler);
        };
      },
      close: () => {
        this.ports.delete(self);
      },
    };
  }
}

/** Typed bus over a channel port, scoped to one workspace. */
export class PopoutBus {
  /**
   * Creates the bus.
   * @param port Channel port.
   * @param workspaceId Workspace the windows share.
   * @param windowId This window's id (own messages are ignored).
   */
  constructor(
    private readonly port: ChannelPort,
    private readonly workspaceId: string,
    readonly windowId: string
  ) {}

  /**
   * Sends a message to the other windows.
   * @param msg Message.
   */
  send(msg: PopoutMessage): void {
    const envelope: PopoutEnvelope = {
      v: 1,
      workspaceId: this.workspaceId,
      from: this.windowId,
      msg,
    };
    try {
      this.port.postMessage(envelope);
    } catch {
      // A closed channel (window unloading) must not break the caller.
    }
  }

  /**
   * Subscribes to valid messages of this workspace from other windows.
   * @param handler Handler receiving the message and the sender id.
   * @return Unsubscribe.
   */
  on(handler: (msg: PopoutMessage, from: string) => void): () => void {
    return this.port.onMessage(data => {
      const envelope = parseEnvelope(data);
      if (
        !envelope ||
        envelope.workspaceId !== this.workspaceId ||
        envelope.from === this.windowId
      ) {
        return;
      }
      handler(envelope.msg, envelope.from);
    });
  }

  /** Closes the underlying port. */
  close(): void {
    this.port.close();
  }
}

/** Rate-limited sender. */
export interface TrailingThrottle<T> {
  /**
   * Offers a value; the latest one is always delivered eventually.
   * @param value Value.
   */
  push(value: T): void;
  /** Drops any pending value and stops the timer. */
  dispose(): void;
}

/**
 * Creates a throttle that forwards at most one value per interval and always
 * delivers the latest value last (so "pointer left" is never dropped).
 * @param send Receiver.
 * @param intervalMs Minimum spacing.
 * @param now Clock.
 * @return Throttled sender with a disposer.
 */
export function createTrailingThrottle<T>(
  send: (value: T) => void,
  intervalMs: number,
  now: () => number = () => Date.now()
): TrailingThrottle<T> {
  let last = -Infinity;
  let pending: {value: T} | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  const flush = () => {
    timer = null;
    if (!pending) return;
    const {value} = pending;
    pending = null;
    last = now();
    send(value);
  };
  return {
    push(value) {
      pending = {value};
      const wait = intervalMs - (now() - last);
      if (wait <= 0 && !timer) flush();
      else if (!timer) timer = setTimeout(flush, Math.max(0, wait));
    },
    dispose() {
      if (timer) clearTimeout(timer);
      timer = null;
      pending = null;
    },
  };
}
