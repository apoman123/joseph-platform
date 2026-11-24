/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for detachable panes: pane keys and links, saved
 * layout parsing, liveness (heartbeats, hidden budgets, crashed and
 * dormant windows, the reload probe), window geometry, the reducer, message
 * validation, the pop-out action queue and the channel bus.
 */

import {describe, expect, it, vi} from 'vitest';
import {
  DETACHED_PANEL_KEY,
  EMPTY_DETACH_STATE,
  HIDDEN_TIMEOUT_MS,
  LIVE_TIMEOUT_MS,
  MAX_PENDING_ACTIONS,
  MemoryChannelHub,
  OPEN_GRACE_MS,
  PopoutBus,
  createTrailingThrottle,
  detachReducer,
  detachedPanelState,
  detachedStatus,
  dormantPanes,
  lostKeys,
  paneKey,
  paneStatus,
  parseEnvelope,
  parseGeometry,
  parsePaneRef,
  parsePending,
  parsePopoutQuery,
  popoutUrl,
  queueAction,
  readDetachedPanes,
  rebaseWorkspace,
  samePanes,
  unappliedActions,
  type DetachState,
  type DetachedPane,
  type PopoutMessage,
  type SequencedAction,
} from './detach';
import {createDefaultWorkspace} from './workspace';

const CHART: DetachedPane = {
  key: 'chart:chart-2',
  ref: {kind: 'chart', chartId: 'chart-2'},
  title: 'ETHUSD 15m',
};
const ALERTS: DetachedPane = {
  key: 'panel:alerts',
  ref: {kind: 'panel', slot: 'bottom-panel', panelId: 'alerts'},
  title: 'Alerts',
};

describe('pane references and links', () => {
  it('keys charts and panels', () => {
    expect(paneKey(CHART.ref)).toBe('chart:chart-2');
    expect(paneKey(ALERTS.ref)).toBe('panel:alerts');
  });

  it('round-trips pop-out links', () => {
    for (const pane of [CHART, ALERTS]) {
      const url = popoutUrl(pane.ref, 'default');
      expect(url.startsWith('/popout?')).toBe(true);
      expect(parsePopoutQuery(url.slice(url.indexOf('?')))).toEqual({
        ref: pane.ref,
        workspaceId: 'default',
      });
    }
  });

  it('rejects damaged links and panels that cannot be detached', () => {
    expect(parsePopoutQuery('?pane=chart:../x')).toBeNull();
    expect(
      parsePopoutQuery('?pane=panel:trading.ticket&slot=side-panel')
    ).toBeNull();
    expect(parsePopoutQuery('?pane=panel:alerts&slot=top')).toBeNull();
    expect(parsePopoutQuery('?pane=chart:chart-1&ws=a b')).toBeNull();
    expect(parsePaneRef({kind: 'chart'})).toBeNull();
  });
});

describe('saved layout', () => {
  it('reads detached panes defensively and drops duplicates', () => {
    const panels = {
      [DETACHED_PANEL_KEY]: {
        panes: [
          CHART,
          {...CHART, title: 'again'},
          {ref: {kind: 'panel', slot: 'side-panel', panelId: 'nope'}},
          'garbage',
          ALERTS,
        ],
      },
    };
    expect(readDetachedPanes(panels)).toEqual([CHART, ALERTS]);
    expect(readDetachedPanes(undefined)).toEqual([]);
    expect(readDetachedPanes({[DETACHED_PANEL_KEY]: 'x'})).toEqual([]);
    expect(
      readDetachedPanes({[DETACHED_PANEL_KEY]: detachedPanelState([ALERTS])})
    ).toEqual([ALERTS]);
    expect(samePanes([CHART, ALERTS], [CHART, ALERTS])).toBe(true);
    expect(samePanes([CHART], [ALERTS])).toBe(false);
  });
});

describe('liveness', () => {
  const now = 1_000_000;

  it('waits for a new window, then follows heartbeats', () => {
    expect(detachedStatus({openedAtMs: now}, now + 1000)).toBe('opening');
    expect(detachedStatus({openedAtMs: now}, now + OPEN_GRACE_MS + 1)).toBe(
      'lost'
    );
    expect(detachedStatus({seenMs: now}, now + LIVE_TIMEOUT_MS)).toBe('live');
    expect(detachedStatus({seenMs: now}, now + LIVE_TIMEOUT_MS + 1)).toBe(
      'lost'
    );
  });

  it('gives hidden (throttled) windows a longer budget', () => {
    expect(
      detachedStatus({seenMs: now, hidden: true}, now + LIVE_TIMEOUT_MS * 3)
    ).toBe('live');
    expect(
      detachedStatus({seenMs: now, hidden: true}, now + HIDDEN_TIMEOUT_MS + 1)
    ).toBe('lost');
  });

  it('keeps restored panes dormant until a window claims them', () => {
    expect(detachedStatus(undefined, now)).toBe('dormant');
  });
});

describe('detach reducer', () => {
  const now = 5_000_000;

  it('opens, sees, closes and expires panes', () => {
    let state: DetachState = EMPTY_DETACH_STATE;
    state = detachReducer(state, {type: 'open', pane: CHART, nowMs: now});
    expect(state.panes).toEqual([CHART]);
    expect(detachedStatus(state.live[CHART.key], now)).toBe('opening');
    state = detachReducer(state, {
      type: 'seen',
      key: CHART.key,
      ref: CHART.ref,
      title: CHART.title,
      hidden: false,
      nowMs: now + 500,
    });
    expect(detachedStatus(state.live[CHART.key], now + 600)).toBe('live');
    // A crashed window stops sending heartbeats and is re-docked.
    expect(lostKeys(state, now + 500 + LIVE_TIMEOUT_MS + 1)).toEqual([
      CHART.key,
    ]);
    const expired = detachReducer(state, {
      type: 'expire',
      nowMs: now + 500 + LIVE_TIMEOUT_MS + 1,
    });
    expect(expired.panes).toEqual([]);
    expect(expired.live).toEqual({});
    const closed = detachReducer(state, {type: 'close', key: CHART.key});
    expect(closed.panes).toEqual([]);
    expect(detachReducer(closed, {type: 'close', key: CHART.key})).toBe(closed);
  });

  it('restores saved panes as dormant and lets surviving windows claim them', () => {
    let state = detachReducer(EMPTY_DETACH_STATE, {
      type: 'restore',
      panes: [CHART, ALERTS],
    });
    expect(lostKeys(state, Date.now())).toEqual([]);
    expect(detachReducer(state, {type: 'expire', nowMs: now})).toBe(state);
    state = detachReducer(state, {
      type: 'seen',
      key: ALERTS.key,
      ref: ALERTS.ref,
      title: ALERTS.title,
      hidden: true,
      nowMs: now,
    });
    expect(detachedStatus(state.live[ALERTS.key], now)).toBe('live');
    expect(detachedStatus(state.live[CHART.key], now)).toBe('dormant');
    // A pop-out the main window forgot is adopted from its hello.
    const other: DetachedPane = {
      key: 'panel:watchlist',
      ref: {kind: 'panel', slot: 'side-panel', panelId: 'watchlist'},
      title: 'Watchlist',
    };
    state = detachReducer(state, {
      type: 'seen',
      key: other.key,
      ref: other.ref,
      title: other.title,
      hidden: false,
      nowMs: now,
    });
    expect(state.panes.map(p => p.key)).toEqual([
      CHART.key,
      ALERTS.key,
      other.key,
    ]);
  });
});

describe('channel protocol', () => {
  it('accepts only well-formed messages', () => {
    const env = (msg: unknown) => ({v: 1, workspaceId: 'w', from: 'a', msg});
    expect(parseEnvelope(env({type: 'main-hello'}))).not.toBeNull();
    expect(parseEnvelope(env({type: 'theme', theme: 'dark'}))).not.toBeNull();
    expect(parseEnvelope(env({type: 'theme', theme: 'neon'}))).toBeNull();
    expect(parseEnvelope(env({type: 'orderMode', mode: 'LIVE'}))).toBeNull();
    expect(parseEnvelope(env({type: 'action', action: {}}))).toBeNull();
    expect(
      parseEnvelope(
        env({
          type: 'popout-hello',
          key: 'chart:chart-9',
          ref: CHART.ref,
          title: 'x',
          hidden: false,
        })
      )
    ).toBeNull();
    expect(parseEnvelope({...env({type: 'main-hello'}), v: 2})).toBeNull();
    expect(parseEnvelope(null)).toBeNull();
  });

  it('delivers between windows of one workspace, never to the sender', () => {
    const hub = new MemoryChannelHub();
    const main = new PopoutBus(hub.port(), 'default', 'main');
    const popout = new PopoutBus(hub.port(), 'default', 'popout-1');
    const stranger = new PopoutBus(hub.port(), 'other', 'popout-2');
    const atMain: PopoutMessage[] = [];
    const atPopout: PopoutMessage[] = [];
    const atStranger: PopoutMessage[] = [];
    main.on(msg => void atMain.push(msg));
    popout.on(msg => void atPopout.push(msg));
    stranger.on(msg => void atStranger.push(msg));
    main.send({type: 'theme', theme: 'light'});
    popout.send({
      type: 'action',
      action: {type: 'setTimeframe', chartId: 'chart-2', timeframe: '1M'},
    });
    expect(atPopout).toEqual([{type: 'theme', theme: 'light'}]);
    expect(atMain).toEqual([
      {
        type: 'action',
        action: {type: 'setTimeframe', chartId: 'chart-2', timeframe: '1M'},
      },
    ]);
    expect(atStranger).toEqual([]);
    popout.close();
    main.send({type: 'main-heartbeat'});
    expect(atPopout).toHaveLength(1);
  });
});

describe('trailing throttle', () => {
  it('limits the rate and always delivers the last value', () => {
    vi.useFakeTimers();
    let clock = 0;
    const sent: Array<number | null> = [];
    const throttle = createTrailingThrottle<number | null>(
      v => void sent.push(v),
      40,
      () => clock
    );
    throttle.push(1);
    throttle.push(2);
    throttle.push(null);
    expect(sent).toEqual([1]);
    clock = 40;
    vi.advanceTimersByTime(40);
    expect(sent).toEqual([1, null]);
    throttle.dispose();
    vi.useRealTimers();
  });
});

describe('reload probe', () => {
  const now = 7_000_000;

  it('shows restored panes as reconnecting until the probe ends', () => {
    expect(detachedStatus(undefined, now, true)).toBe('reconnecting');
    let state = detachReducer(EMPTY_DETACH_STATE, {
      type: 'restore',
      panes: [CHART, ALERTS],
    });
    state = detachReducer(state, {type: 'probe', probing: true});
    expect(paneStatus(state, CHART.key, now)).toBe('reconnecting');
    expect(dormantPanes(state, now)).toEqual([]);
    expect(lostKeys(state, now + 600_000)).toEqual([]);
    expect(detachReducer(state, {type: 'probe', probing: true})).toBe(state);
    // A surviving window claims its pane during the probe.
    state = detachReducer(state, {
      type: 'seen',
      key: ALERTS.key,
      ref: ALERTS.ref,
      title: ALERTS.title,
      hidden: false,
      nowMs: now,
    });
    expect(paneStatus(state, ALERTS.key, now)).toBe('live');
    state = detachReducer(state, {type: 'probe', probing: false});
    expect(paneStatus(state, CHART.key, now)).toBe('dormant');
    expect(dormantPanes(state, now)).toEqual([CHART]);
  });
});

describe('window geometry', () => {
  const geometry = {left: 40, top: 60, width: 1100, height: 720};

  it('accepts plausible integer geometry only', () => {
    expect(parseGeometry(geometry)).toEqual(geometry);
    expect(parseGeometry({...geometry, left: 40.4})).toEqual(geometry);
    expect(parseGeometry({...geometry, width: 20})).toBeNull();
    expect(parseGeometry({...geometry, top: 1e9})).toBeNull();
    expect(parseGeometry({...geometry, height: Number.NaN})).toBeNull();
    expect(parseGeometry({left: 1, top: 2})).toBeNull();
    expect(parseGeometry('100x100')).toBeNull();
  });

  it('remembers geometry with the layout and updates it from heartbeats', () => {
    const panels = {
      [DETACHED_PANEL_KEY]: {
        panes: [
          {...CHART, geometry},
          {...ALERTS, geometry: {left: 'x'}},
        ],
      },
    };
    const panes = readDetachedPanes(panels);
    expect(panes[0]?.geometry).toEqual(geometry);
    expect(panes[1]?.geometry).toBeUndefined();
    expect(detachedPanelState(panes).panes[0]?.geometry).toEqual(geometry);
    const moved = {...geometry, left: 900};
    let state = detachReducer(EMPTY_DETACH_STATE, {
      type: 'restore',
      panes,
    });
    const seen = (g: typeof geometry | null) =>
      detachReducer(state, {
        type: 'seen',
        key: CHART.key,
        ref: CHART.ref,
        title: CHART.title,
        hidden: false,
        nowMs: 1,
        geometry: g,
      });
    expect(seen(geometry).panes).toBe(state.panes);
    state = seen(moved);
    expect(state.panes[0]?.geometry).toEqual(moved);
    expect(samePanes(state.panes, panes)).toBe(false);
    expect(samePanes(panes, readDetachedPanes(panels))).toBe(true);
  });
});

describe('pop-out action queue', () => {
  const range = (chartId: string, from: number): SequencedAction['action'] => ({
    type: 'setVisibleRange',
    chartId,
    range: {from_ms: from, to_ms: from + 1},
  });

  it('keeps actions in order, coalesces viewport changes and caps the queue', () => {
    let queue: SequencedAction[] = [];
    queue = queueAction(queue, {seq: 1, action: range('chart-1', 1)});
    queue = queueAction(queue, {
      seq: 2,
      action: {type: 'setTimeframe', chartId: 'chart-1', timeframe: '1h'},
    });
    queue = queueAction(queue, {seq: 3, action: range('chart-2', 3)});
    queue = queueAction(queue, {seq: 4, action: range('chart-1', 4)});
    expect(queue.map(e => e.seq)).toEqual([2, 3, 4]);
    for (let seq = 5; seq < MAX_PENDING_ACTIONS + 20; seq += 1) {
      queue = queueAction(queue, {
        seq,
        action: {type: 'toggleVolume', chartId: 'chart-1'},
      });
    }
    expect(queue).toHaveLength(MAX_PENDING_ACTIONS);
    expect(queue.at(-1)?.seq).toBe(MAX_PENDING_ACTIONS + 19);
    expect(unappliedActions(queue, MAX_PENDING_ACTIONS + 17)).toHaveLength(2);
  });

  it('validates pending actions from the channel', () => {
    const ok = [
      {seq: 1, action: {type: 'toggleVolume', chartId: 'chart-1'}},
      {seq: 3, action: {type: 'rename', name: 'x'}},
    ];
    expect(parsePending(ok)).toEqual(ok);
    expect(parsePending([...ok].reverse())).toBeNull();
    expect(parsePending([{seq: 0, action: {type: 'rename'}}])).toBeNull();
    expect(parsePending([{seq: 1, action: {}}])).toBeNull();
    expect(parsePending([null])).toBeNull();
    expect(parsePending('nope')).toBeNull();
    expect(
      parsePending(
        Array.from({length: MAX_PENDING_ACTIONS + 1}, (_, i) => ({
          seq: i + 1,
          action: {type: 'toggleVolume', chartId: 'chart-1'},
        }))
      )
    ).toBeNull();
  });

  it('re-applies unacknowledged actions on an adopted workspace', () => {
    const doc = createDefaultWorkspace({
      workspaceId: 'default',
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: '5m',
      nowMs: 1,
    });
    const pending: SequencedAction[] = [
      {
        seq: 1,
        action: {type: 'setTimeframe', chartId: 'chart-1', timeframe: '1h'},
      },
      {seq: 2, action: {type: 'toggleVolume', chartId: 'chart-1'}},
    ];
    const rebased = rebaseWorkspace(doc, pending, 9);
    expect(rebased.charts[0]?.timeframe).toBe('1h');
    expect(rebased.charts[0]?.show_volume).toBe(false);
    expect(rebaseWorkspace(doc, [], 9)).toBe(doc);
  });

  it('accepts acknowledgements and pending actions on the wire', () => {
    const env = (msg: unknown) => ({v: 1, workspaceId: 'w', from: 'a', msg});
    const doc = {schema_version: 1};
    expect(parseEnvelope(env({type: 'main-bye'}))).not.toBeNull();
    expect(
      parseEnvelope(env({type: 'workspace', doc, applied: {'popout-1': 3}}))
    ).not.toBeNull();
    expect(
      parseEnvelope(env({type: 'workspace', doc, applied: {'popout-1': -1}}))
    ).toBeNull();
    const pending = [{seq: 1, action: {type: 'toggleVolume', chartId: 'c'}}];
    expect(
      parseEnvelope(env({type: 'action', action: pending[0]?.action, pending}))
    ).not.toBeNull();
    expect(
      parseEnvelope(
        env({type: 'action', action: {type: 'rename'}, pending: [{seq: 1}]})
      )
    ).toBeNull();
    const hello = {
      type: 'popout-hello',
      key: CHART.key,
      ref: CHART.ref,
      title: 'x',
      hidden: false,
    };
    expect(parseEnvelope(env({...hello, pending}))).not.toBeNull();
    expect(parseEnvelope(env({...hello, pending: 'x'}))).toBeNull();
  });
});
