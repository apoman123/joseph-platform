/**
 * @fileoverview Tests for detachable panes: pane keys and links, saved
 * layout parsing, liveness (heartbeats, hidden budgets, crashed and
 * dormant windows), the reducer, message validation and the channel bus.
 */
import {describe, expect, it, vi} from 'vitest';
import {
  DETACHED_PANEL_KEY,
  EMPTY_DETACH_STATE,
  HIDDEN_TIMEOUT_MS,
  LIVE_TIMEOUT_MS,
  MemoryChannelHub,
  OPEN_GRACE_MS,
  PopoutBus,
  createTrailingThrottle,
  detachReducer,
  detachedPanelState,
  detachedStatus,
  lostKeys,
  paneKey,
  parseEnvelope,
  parsePaneRef,
  parsePopoutQuery,
  popoutUrl,
  readDetachedPanes,
  samePanes,
  type DetachState,
  type DetachedPane,
  type PopoutMessage,
} from './detach';

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
    main.on(msg => atMain.push(msg));
    popout.on(msg => atPopout.push(msg));
    stranger.on(msg => atStranger.push(msg));
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
    const sent: (number | null)[] = [];
    const throttle = createTrailingThrottle<number | null>(
      v => sent.push(v),
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
