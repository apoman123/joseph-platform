/**
 * @fileoverview One chart pane: mounts the chart port, loads candles,
 * follows the stream, hosts drawings and feature overlay layers, and keeps
 * provenance visible at all times.
 */
'use client';
import type {ChartBar, ChartPort, OverlayLayer} from '@joseph/chart-core';
import {themeFromCss} from '@joseph/chart-core';
import {
  TIMEFRAME_MS,
  VENUE_NAMES,
  parseInstrumentId,
  shiftTimeframe,
  type Candle,
  type ChartState,
  type FeedMode,
  type FeedState,
  type Instrument,
  type StreamStatus,
} from '@joseph/contracts';
import {
  chooseReplayStart,
  formatCountdown,
  msUntilBarClose,
  nextReplaySpeed,
  replayAsOfMs,
  replayAtEnd,
  replayVisibleBars,
  stepReplay,
  timeframeTitle,
  type ReplayState,
} from '@joseph/terminal-shell';
import {X} from 'lucide-react';
import {
  DrawingController,
  DrawingRepository,
  DrawingSeriesSync,
  DrawingStore,
  bindAutosave,
  seriesAsOf,
} from '@joseph/drawing-core';
import {ids} from './ids';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {toChartBar as candleToBar} from './chart-bars';
import {
  useCompare,
  useDeltaPane,
  useSessionSeparators,
} from './chart-pane-hooks';
import {ChartDataWindow} from './data-window';
import {DrawingStyleBar} from './drawing-style-bar';
import {formatAge, formatPrice, formatUtcTime} from './format';
import {ProvenanceBadge, ProvenanceRibbon} from './provenance';
import {useStore} from './react-bindings';
import {ReplayBar} from './replay-bar';

/** Bars requested on load. */
const HISTORY_BARS = 500;
/** Bars shown when a chart opens without a saved viewport. */
const INITIAL_VISIBLE_BARS = 140;
/** Age after which a live feed counts as stale. */
const STALE_AFTER_MS = 60_000;
/** Feed modes offered per chart (REPLAY is entered with bar replay). */
const FEED_MODES: readonly [FeedMode, string][] = [
  ['AUTO', 'Auto feed'],
  ['NATIVE_WS', 'WebSocket'],
  ['REST_POLL', 'REST polling'],
];
/** Link groups cycled from the pane header. */
const LINK_GROUPS: readonly ChartState['link_group'][] = [
  null,
  'A',
  'B',
  'C',
  'D',
];

/** Pane load status. */
interface PaneStatus {
  /** Loading phase. */
  phase: 'loading' | 'ready' | 'error' | 'empty';
  /** Provenance of what is displayed. */
  feedState: FeedState;
  /** Actionable error. */
  error: string | null;
  /** When the latest data arrived. */
  lastUpdateMs: number | null;
}

/** Context-menu request. */
export interface PaneMenu {
  /** X within the pane. */
  x: number;
  /** Y within the pane. */
  y: number;
  /** Price under the pointer. */
  price: number | null;
  /** Time under the pointer. */
  timeMs: number | null;
}

/**
 * Converts a contract candle to a render bar.
 * @param candle Candle.
 * @return Bar (display numbers only).
 */
export function toChartBar(candle: Candle): ChartBar {
  return candleToBar(candle);
}

/**
 * Reads a CSS custom property from the document root.
 * @param name Property name.
 * @return Value.
 */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name);
}

/** Pane props. */
interface ChartPaneProps {
  /** Chart state from the workspace. */
  chart: ChartState;
  /** Whether this is the active chart. */
  active: boolean;
  /** Runtime. */
  runtime: TerminalRuntime;
  /** Opens the context menu. */
  onMenu: (chartId: string, menu: PaneMenu | null) => void;
  /** Extra header buttons (e.g. open in another window). */
  headerActions?: ReactNode;
}

/**
 * Chart pane component.
 * @param props Props.
 * @return Pane.
 */
export function ChartPane({
  chart,
  active,
  runtime,
  onMenu,
  headerActions,
}: ChartPaneProps): ReactElement {
  const hostRef = useRef<HTMLDivElement>(null);
  const [port, setPort] = useState<ChartPort | null>(null);
  const [status, setStatus] = useState<PaneStatus>({
    phase: 'loading',
    feedState: 'CONNECTING',
    error: null,
    lastUpdateMs: null,
  });
  const [instrument, setInstrument] = useState<Instrument | null>(null);
  const [hover, setHover] = useState<ChartBar | null>(null);
  const [lastBar, setLastBar] = useState<ChartBar | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const barsRef = useRef<ChartBar[]>([]);
  /** Bars actually displayed (truncated during replay). */
  const shownRef = useRef<readonly ChartBar[]>([]);
  const [barCount, setBarCount] = useState(0);
  const chartRef = useRef(chart);
  chartRef.current = chart;
  const workspace = useStore(runtime.workspace);
  const theme = useStore(runtime.theme);
  const dataWindow = useStore(runtime.dataWindow);
  const replayStates = useStore(runtime.replay);
  const {chart_id: chartId, instrument_id: instrumentId, timeframe} = chart;
  const replay: ReplayState | null = replayStates[chartId] ?? null;
  const replayRef = useRef(replay);
  replayRef.current = replay;
  // Calendar-aware: a monthly cursor bar closes on the 1st of next month.
  const atMs = replayAsOfMs(replay, timeframe);
  const atMsRef = useRef(atMs);
  atMsRef.current = atMs;
  const tickSize = instrument?.tick_size ?? null;

  // Mount the chart library lazily on the client.
  useEffect(() => {
    let disposed = false;
    let created: ChartPort | null = null;
    void import('@joseph/chart-core/lwc').then(({createLwcChartPort}) => {
      if (disposed || !hostRef.current) return;
      created = createLwcChartPort();
      created.mount(hostRef.current, themeFromCss(cssVar));
      runtime.charts.set(chartId, created);
      setPort(created);
    });
    return () => {
      disposed = true;
      if (created && runtime.charts.get(chartId) === created)
        runtime.charts.delete(chartId);
      created?.destroy();
      setPort(null);
    };
    // Theme changes remount so canvas colors follow the tokens.
  }, [theme, chartId, runtime]);

  // Instrument metadata (tick size for formatting and drawings).
  useEffect(() => {
    let alive = true;
    const {venue, venueMarketId} = parseInstrumentId(instrumentId);
    setInstrument(null);
    runtime.clients.market
      .listInstruments({venue, q: venueMarketId})
      .then(list => {
        if (alive)
          setInstrument(
            list.instruments.find(i => i.instrument_id === instrumentId) ?? null
          );
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [instrumentId, runtime]);

  // Series presentation.
  useEffect(() => {
    port?.setSeries({
      instrumentId,
      timeframe,
      chartType: chart.chart_type,
      priceScale: chart.price_scale,
      tickSize: tickSize ?? '0.01',
      showVolume: chart.show_volume,
    });
  }, [
    port,
    instrumentId,
    timeframe,
    chart.chart_type,
    chart.price_scale,
    chart.show_volume,
    tickSize,
  ]);

  // History and stream. A backend failure never falls back to synthetic data.
  useEffect(() => {
    if (!port) return;
    let alive = true;
    setStatus({
      phase: 'loading',
      feedState: 'CONNECTING',
      error: null,
      lastUpdateMs: null,
    });
    barsRef.current = [];
    setLastBar(null);
    let batchState: FeedState = 'CONNECTING';
    const load = () =>
      runtime.clients.market
        .getCandles({instrumentId, timeframe, limit: HISTORY_BARS})
        .then(batch => {
          if (!alive) return;
          const bars = batch.candles.map(toChartBar);
          barsRef.current = bars;
          const shown = replayVisibleBars(bars, replayRef.current);
          shownRef.current = shown;
          setBarCount(bars.length);
          port.setBars(shown);
          const saved = chartRef.current.visible_range;
          const first = bars[Math.max(0, bars.length - INITIAL_VISIBLE_BARS)];
          const lastStart = bars.at(-1)?.timeMs;
          if (saved) {
            port.setVisibleRange({fromMs: saved.from_ms, toMs: saved.to_ms});
          } else if (first && lastStart !== undefined) {
            port.setVisibleRange({
              fromMs: first.timeMs,
              toMs: shiftTimeframe(timeframe, lastStart, 6),
            });
          } else {
            port.fitContent();
          }
          setLastBar(bars.at(-1) ?? null);
          batchState = batch.feed_state;
          setStatus({
            phase: bars.length ? 'ready' : 'empty',
            feedState: batch.feed_state,
            error: null,
            lastUpdateMs: Date.now(),
          });
        })
        .catch((error: unknown) => {
          if (!alive) return;
          setStatus({
            phase: 'error',
            feedState: 'UNAVAILABLE',
            error: error instanceof Error ? error.message : 'Request failed',
            lastUpdateMs: null,
          });
        });
    void load();
    const unsubscribe = runtime.clients.marketStream.subscribe(
      [
        {
          channel: 'candles',
          instrument_id: instrumentId,
          timeframe,
          feed_mode: chartRef.current.feed_mode,
        },
      ],
      envelope => {
        if (envelope.type === 'snapshot_required') {
          void load();
          return;
        }
        if (envelope.type !== 'candle.updated') return;
        const candle = envelope.payload as unknown as Candle;
        if (
          candle.instrument_id !== instrumentId ||
          candle.timeframe !== timeframe
        )
          return;
        const bar = toChartBar(candle);
        const bars = barsRef.current;
        const last = bars.at(-1);
        if (last && bar.timeMs < last.timeMs) return;
        if (last && bar.timeMs === last.timeMs) bars[bars.length - 1] = bar;
        else bars.push(bar);
        // A closed bar adds a point to engine-backed drawings (VWAP...).
        if (bar.final || (last && bar.timeMs > last.timeMs))
          runtime.drawing.series.get(chartId)?.refresh();
        setBarCount(bars.length);
        // During replay new bars are kept but stay hidden behind the cursor.
        if (replayRef.current) return;
        shownRef.current = bars;
        port.updateBar(bar);
        setLastBar(bar);
        setStatus(s => ({
          ...s,
          phase: 'ready',
          feedState: candle.feed_state,
          lastUpdateMs: Date.now(),
        }));
      }
    );
    const offStatus = runtime.clients.marketStream.onStatus(
      (next: StreamStatus) => {
        // A dropped stream makes displayed live data stale until it resumes.
        if (next === 'RECONNECTING' && batchState !== 'DEMO') {
          setStatus(s => ({
            ...s,
            feedState: s.phase === 'ready' ? 'STALE' : s.feedState,
          }));
        }
      }
    );
    return () => {
      alive = false;
      unsubscribe();
      offStatus();
    };
  }, [port, instrumentId, timeframe, runtime, chartId]);

  // Staleness clock.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const ageMs = status.lastUpdateMs === null ? 0 : now - status.lastUpdateMs;
  const effectiveState: FeedState = replay
    ? 'REPLAY'
    : status.feedState !== 'DEMO' &&
        status.phase === 'ready' &&
        ageMs > STALE_AFTER_MS
      ? 'STALE'
      : status.feedState;
  useEffect(() => {
    runtime.feedStates.update(states =>
      states[chartId] === effectiveState
        ? states
        : {...states, [chartId]: effectiveState}
    );
  }, [runtime, chartId, effectiveState]);
  useEffect(
    () => () =>
      runtime.feedStates.update(({[chartId]: _removed, ...rest}) => rest),
    [runtime, chartId]
  );

  // Drawings for this exact instrument.
  useEffect(() => {
    if (!port) return;
    const repository = new DrawingRepository(
      runtime.storage,
      workspace.workspace_id,
      runtime.tools
    );
    const store = new DrawingStore(repository.load(instrumentId));
    const palette = () => {
      const t = themeFromCss(cssVar);
      return {
        user: t.user,
        text: t.text,
        bid: t.bid,
        ask: t.ask,
        background: t.background,
        fontFamily: t.fontFamily,
        warning: t.warning,
      };
    };
    // Engine-backed drawings request their values from the market engine.
    const series = new DrawingSeriesSync(store, runtime.tools, {
      fetch: query => runtime.clients.market.getIndicatorSeries(query),
      instrumentId: () => instrumentId,
      timeframe: () => timeframe,
      onChange: () => {
        port.invalidate();
        runtime.drawing.revision.update(n => n + 1);
      },
    });
    const controller = new DrawingController(port, store, runtime.tools, {
      instrumentId: () => instrumentId,
      timeframe: () => timeframe,
      tickSize: () => tickSize,
      bars: () => barsRef.current,
      durationMs: () => TIMEFRAME_MS[timeframe],
      magnet: () => runtime.drawing.magnet.get(),
      palette,
      now: () => Date.now(),
      newId: ids.next,
      series: doc => seriesAsOf(series.get(doc.drawing_id), atMsRef.current),
      onToolChange: tool => {
        if (runtime.selection.get().chartId === chartId)
          runtime.drawing.activeTool.set(tool);
      },
      onSelectionChange: drawingId => {
        const current = runtime.drawing.selection.get();
        if (drawingId) runtime.drawing.selection.set({chartId, drawingId});
        else if (current?.chartId === chartId)
          runtime.drawing.selection.set(null);
      },
    });
    const offAutosave = bindAutosave(store, repository, instrumentId);
    const offStore = store.subscribe(() =>
      runtime.drawing.revision.update(n => n + 1)
    );
    runtime.drawing.controllers.set(chartId, controller);
    runtime.drawing.stores.set(chartId, store);
    runtime.drawing.series.set(chartId, series);
    const armed = runtime.drawing.activeTool.get();
    if (armed && runtime.selection.get().chartId === chartId)
      controller.setTool(armed);
    runtime.drawing.revision.update(n => n + 1);
    return () => {
      offStore();
      offAutosave();
      if (runtime.drawing.selection.get()?.chartId === chartId)
        runtime.drawing.selection.set(null);
      controller.dispose();
      series.dispose();
      if (runtime.drawing.series.get(chartId) === series)
        runtime.drawing.series.delete(chartId);
      if (runtime.drawing.controllers.get(chartId) === controller)
        runtime.drawing.controllers.delete(chartId);
      if (runtime.drawing.stores.get(chartId) === store)
        runtime.drawing.stores.delete(chartId);
    };
    // tickSize is read lazily through the accessor.
  }, [port, instrumentId, timeframe, workspace.workspace_id, chartId, runtime]); // eslint-disable-line react-hooks/exhaustive-deps

  // Feature overlay layers.
  const layerVisibility = useMemo(
    () =>
      new Map(
        chart.indicator_layers.map(layer => [
          `indicator-viz:${layer.plugin_id}`,
          layer.visible,
        ])
      ),
    [chart.indicator_layers]
  );
  const visibilityRef = useRef(layerVisibility);
  visibilityRef.current = layerVisibility;
  const latestLayers = useRef(new Map<string, OverlayLayer>());
  useEffect(() => {
    if (!port) return;
    const latest = latestLayers.current;
    const applied = new Set<string>();
    const apply = (layer: OverlayLayer) => {
      latestLayers.current.set(layer.layerId, layer);
      const visible = visibilityRef.current.get(layer.layerId) ?? true;
      port.setOverlayLayer({...layer, visible: layer.visible && visible});
      applied.add(layer.layerId);
    };
    const subscriptions: (() => void)[] = [];
    const attach = () => {
      for (const off of subscriptions.splice(0)) off();
      for (const id of applied) port.removeOverlayLayer(id);
      applied.clear();
      for (const provider of runtime.chartLayers.providers.get()) {
        const store = provider({chartId, instrumentId, timeframe, atMs});
        let current: string | null = null;
        const handle = (layer: OverlayLayer | null) => {
          if (current && (!layer || layer.layerId !== current)) {
            port.removeOverlayLayer(current);
            applied.delete(current);
          }
          current = layer?.layerId ?? null;
          if (layer) apply(layer);
        };
        subscriptions.push(store.subscribe(handle));
        handle(store.get());
      }
    };
    attach();
    const offProviders = runtime.chartLayers.providers.subscribe(attach);
    return () => {
      offProviders();
      for (const off of subscriptions.splice(0)) off();
      for (const id of applied) port.removeOverlayLayer(id);
      latest.clear();
    };
  }, [port, chartId, instrumentId, timeframe, atMs, runtime]);
  useEffect(() => {
    if (!port) return;
    for (const layer of latestLayers.current.values()) {
      const visible = layerVisibility.get(layer.layerId) ?? true;
      port.setOverlayLayer({...layer, visible: layer.visible && visible});
    }
  }, [port, layerVisibility]);

  // Crosshair sync, hover OHLC, context menu and overlay drags.
  useEffect(() => {
    if (!port) return;
    const offMove = port.on('crosshairMove', point => {
      if (point?.timeMs != null) {
        const bars = barsRef.current;
        const found = bars.find(bar => bar.timeMs === point.timeMs) ?? null;
        setHover(found);
      } else {
        setHover(null);
      }
      runtime.crosshair.emit({
        sourceChartId: chartId,
        point:
          point && point.timeMs != null && point.price != null
            ? {timeMs: point.timeMs, price: point.price}
            : null,
      });
    });
    const offSync = runtime.crosshair.subscribe(({sourceChartId, point}) => {
      if (sourceChartId === chartId || !runtime.workspace.get().sync.crosshair)
        return;
      port.setCrosshair(point);
    });
    const offMenu = port.on('contextMenu', event => {
      if (replayRef.current) return;
      onMenu(chartId, {
        x: event.x,
        y: event.y,
        price: event.price,
        timeMs: event.timeMs,
      });
      runtime.events.emit('chart.contextMenu', {
        chartId,
        instrumentId,
        price: event.price ?? 0,
        timeMs: event.timeMs,
      });
    });
    const offDrag = port.on('overlayDragEnd', event => {
      // Orders are never changed from a replayed chart.
      if (replayRef.current) return;
      runtime.events.emit('chart.overlayDragEnd', {chartId, ...event});
    });
    const offClick = port.on('click', event => {
      const state = replayRef.current;
      if (state?.phase !== 'select' || event.timeMs === null) return;
      const times = barsRef.current.map(bar => bar.timeMs);
      runtime.replay.update(all => ({
        ...all,
        [chartId]: chooseReplayStart(state, times, event.timeMs),
      }));
    });
    let rangeTimer: ReturnType<typeof setTimeout> | null = null;
    const offRange = port.on('visibleRangeChange', range => {
      if (rangeTimer) clearTimeout(rangeTimer);
      rangeTimer = setTimeout(() => {
        if (!range) return;
        const saved = chartRef.current.visible_range;
        if (
          saved &&
          Math.abs(saved.from_ms - range.fromMs) < 1000 &&
          Math.abs(saved.to_ms - range.toMs) < 1000
        )
          return;
        runtime.dispatch({
          type: 'setVisibleRange',
          chartId,
          range: {
            from_ms: Math.max(0, Math.round(range.fromMs)),
            to_ms: Math.max(0, Math.round(range.toMs)),
          },
        });
      }, 600);
    });
    return () => {
      offMove();
      offSync();
      offMenu();
      offDrag();
      offClick();
      offRange();
      if (rangeTimer) clearTimeout(rangeTimer);
    };
  }, [port, chartId, instrumentId, runtime, onMenu]);

  // Viewport sync from other charts.
  const range = chart.visible_range;
  useEffect(() => {
    if (!port || !range || !workspace.sync.viewport) return;
    const current = port.serialize().visibleRange;
    if (
      current &&
      Math.abs(current.fromMs - range.from_ms) < 1000 &&
      Math.abs(current.toMs - range.to_ms) < 1000
    )
      return;
    port.setVisibleRange({fromMs: range.from_ms, toMs: range.to_ms});
  }, [port, range, workspace.sync.viewport]);

  // Replay: show bars up to the cursor; restore everything on exit.
  const replayCursor = replay?.cursorMs ?? null;
  const replaying = replay !== null;
  useEffect(() => {
    if (!port || status.phase !== 'ready') return;
    const all = barsRef.current;
    const shownBars = replayVisibleBars(all, replayRef.current);
    if (shownBars === shownRef.current && !replaying) return;
    shownRef.current = shownBars;
    port.setBars(shownBars);
    setLastBar(shownBars.at(-1) ?? null);
    port.invalidate();
  }, [port, replayCursor, replaying, status.phase]);
  const replayPhase = replay?.phase ?? null;
  const replaySpeed = replay?.speed ?? 1;
  const updateReplay = useCallback(
    (change: (state: ReplayState) => ReplayState) =>
      runtime.replay.update(all => {
        const current = all[chartId];
        return current ? {...all, [chartId]: change(current)} : all;
      }),
    [runtime, chartId]
  );
  useEffect(() => {
    if (replayPhase !== 'playing') return;
    const timer = setInterval(() => {
      const times = barsRef.current.map(bar => bar.timeMs);
      updateReplay(state => stepReplay(state, times, 1));
    }, 1000 / replaySpeed);
    return () => clearInterval(timer);
  }, [replayPhase, replaySpeed, updateReplay]);
  // Leaving the instrument or timeframe ends replay on this chart.
  useEffect(
    () => () => runtime.replay.update(({[chartId]: _ended, ...rest}) => rest),
    [runtime, chartId, instrumentId, timeframe]
  );

  const symbolOf = useCallback(
    (id: string) => parseInstrumentId(id).venueMarketId,
    []
  );
  const delta = useDeltaPane(port, chart, runtime, atMs);
  const compare = useCompare(port, chart, runtime, symbolOf);
  useSessionSeparators(
    port,
    chart.session_separators === true,
    shownRef,
    timeframe
  );

  const shown = hover ?? lastBar;
  const direction = shown && shown.close >= shown.open ? 'j-pos' : 'j-neg';
  const {venue, venueMarketId} = parseInstrumentId(instrumentId);
  const shownBars = shownRef.current;
  const shownIndex = shown
    ? shownBars.findIndex(bar => bar.timeMs === shown.timeMs)
    : -1;
  const previousClose =
    shownIndex > 0 ? (shownBars[shownIndex - 1]?.close ?? null) : null;
  const linkIndex = LINK_GROUPS.indexOf(chart.link_group ?? null);
  return (
    <section
      className={`pane ${active ? 'pane--active' : ''}`}
      aria-label={`Chart ${instrument?.display_symbol ?? venueMarketId} ${timeframe}`}
      data-testid={`chart-pane-${chartId}`}
      data-active={active}
      onPointerDownCapture={() => {
        if (!active) runtime.dispatch({type: 'setActiveChart', chartId});
      }}
    >
      <ProvenanceRibbon state={effectiveState} />
      <header className="pane__header">
        <div className="pane__title">
          <strong className="pane__symbol">
            {instrument?.display_symbol ?? venueMarketId}
          </strong>
          <span className="pane__chip">{VENUE_NAMES[venue]}</span>
          <span className="pane__chip" title={timeframeTitle(timeframe)}>
            {timeframe}
          </span>
          {!replay && status.phase === 'ready' && (
            <span
              className="pane__chip pane__countdown j-tabular"
              title="Time until this bar closes (UTC calendar)"
              data-testid="bar-countdown"
            >
              {formatCountdown(msUntilBarClose(timeframe, now))}
            </span>
          )}
          <button
            type="button"
            className="pane__chip pane__chip--button"
            data-testid="link-group"
            title="Link group: linked charts follow each other when sync is on"
            aria-label={`${chart.link_group ? `Link ${chart.link_group}` : 'Unlinked'}: change link group`}
            onClick={() =>
              runtime.dispatch({
                type: 'setLinkGroup',
                chartId,
                group:
                  LINK_GROUPS[(linkIndex + 1) % LINK_GROUPS.length] ?? null,
              })
            }
          >
            {chart.link_group ? `Link ${chart.link_group}` : 'Unlinked'}
          </button>
          <label className="sr-only" htmlFor={`feed-mode-${chartId}`}>
            Feed mode
          </label>
          <select
            id={`feed-mode-${chartId}`}
            className="pane__feed"
            value={chart.feed_mode === 'REPLAY' ? 'AUTO' : chart.feed_mode}
            title="Requested market-data transport"
            onChange={event =>
              runtime.dispatch({
                type: 'setFeedMode',
                chartId,
                feedMode: event.target.value as FeedMode,
              })
            }
          >
            {FEED_MODES.map(([mode, label]) => (
              <option key={mode} value={mode}>
                {label}
              </option>
            ))}
          </select>
          {compare.map(entry => (
            <span
              key={entry.instrumentId}
              className="pane__compare"
              style={{borderColor: entry.color, color: entry.color}}
              data-testid={`compare-chip-${entry.instrumentId}`}
              title={entry.error ?? `Compared on the left % axis`}
            >
              {symbolOf(entry.instrumentId)}
              {entry.error ? ' (unavailable)' : ''}
              <button
                type="button"
                aria-label={`Stop comparing ${symbolOf(entry.instrumentId)}`}
                onClick={() =>
                  runtime.dispatch({
                    type: 'setCompare',
                    chartId,
                    instrumentIds: (chart.compare ?? []).filter(
                      id => id !== entry.instrumentId
                    ),
                  })
                }
              >
                <X size={11} aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
        <dl
          className="pane__ohlc j-tabular"
          aria-label="Open, high, low, close"
        >
          {(['open', 'high', 'low', 'close'] as const).map(key => (
            <div key={key}>
              <dt>{key.charAt(0).toUpperCase()}</dt>
              <dd className={key === 'close' ? direction : undefined}>
                {shown ? formatPrice(String(shown[key]), tickSize) : '—'}
              </dd>
            </div>
          ))}
        </dl>
        <div className="pane__meta">
          {headerActions}
          <ProvenanceBadge state={effectiveState} ageMs={ageMs} />
          {status.lastUpdateMs !== null && (
            <span className="pane__asof j-tabular">
              as of {formatUtcTime(status.lastUpdateMs, true)}
            </span>
          )}
        </div>
      </header>
      <div
        className="pane__canvas"
        ref={hostRef}
        data-testid="chart-canvas"
        data-replay={replay?.phase ?? 'off'}
      />
      {dataWindow && status.phase === 'ready' && (
        <ChartDataWindow
          runtime={runtime}
          chartId={chartId}
          atMs={atMs}
          bar={shown}
          timeframe={timeframe}
          previousClose={previousClose}
          tickSize={tickSize}
          delta={delta.shown ? delta.bars : null}
          compare={compare}
          label={symbolOf}
        />
      )}
      {active && <DrawingStyleBar chartId={chartId} />}
      {replay && (
        <ReplayBar
          state={replay}
          shown={replay.cursorMs === null ? barCount : shownBars.length}
          total={barCount}
          atEnd={replayAtEnd(
            replay,
            barsRef.current.map(bar => bar.timeMs)
          )}
          onStep={step =>
            updateReplay(state =>
              stepReplay(
                state,
                barsRef.current.map(bar => bar.timeMs),
                step
              )
            )
          }
          onPlayPause={() =>
            updateReplay(state => ({
              ...state,
              phase: state.phase === 'playing' ? 'paused' : 'playing',
            }))
          }
          onSpeed={() => updateReplay(nextReplaySpeed)}
          onStartDefault={() =>
            updateReplay(state =>
              chooseReplayStart(
                state,
                barsRef.current.map(bar => bar.timeMs),
                null
              )
            )
          }
          onExit={() =>
            runtime.replay.update(({[chartId]: _ended, ...rest}) => rest)
          }
        />
      )}
      {status.phase === 'loading' && (
        <div className="pane__overlay j-muted">Loading candles…</div>
      )}
      {status.phase === 'empty' && (
        <div className="pane__overlay">
          <div className="j-empty">
            <strong>No candles for this range</strong>
            <p>
              The venue returned no bars for {venueMarketId} on {timeframe}. Try
              another timeframe.
            </p>
          </div>
        </div>
      )}
      {status.phase === 'error' && (
        <div className="pane__overlay" role="alert">
          <div className="j-empty">
            <strong>Candles didn’t load</strong>
            <p>{status.error}</p>
            <p>
              The chart stays empty instead of showing synthetic prices. Check
              the market engine connection, then reload.
            </p>
          </div>
        </div>
      )}
      {effectiveState === 'STALE' && status.phase === 'ready' && (
        <div className="pane__stale j-notice j-notice--danger" role="status">
          No update for {formatAge(ageMs)}. Prices may be out of date; new
          exposure is blocked server-side until the feed recovers.
        </div>
      )}
    </section>
  );
}
