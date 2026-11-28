/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview One chart pane: mounts the chart port, loads candles,
 * follows the stream, hosts drawings and feature overlay layers, and keeps
 * provenance visible at all times.
 *
 * The pane is a labelled region (`Chart <symbol> <timeframe>`); pressing
 * anywhere in it makes it the active chart. Header controls are native
 * buttons and a labelled select. Load errors are announced with
 * `role="alert"`, a stale feed with `role="status"`.
 */

'use client';
import {
  themeFromCss,
  type ChartBar,
  type ChartPort,
  type OverlayLayer,
} from '@joseph/chart-core';
import {
  EXCHANGE_HOURS,
  TIMEFRAME_MS,
  VENUE_NAMES,
  instrumentCalendarId,
  isExchangeCalendar,
  parseInstrumentId,
  type Candle,
  type ChartState,
  type FeedMode,
  type FeedState,
  type Instrument,
  type PriceAdjustment,
  type StreamStatus,
} from '@joseph/contracts';
import {
  barCountdown,
  chooseReplayStart,
  formatCountdown,
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
import {StudyLegend, useStudyRuns} from './python-studies';
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
import {useCompare, useSessionSeparators} from './chart-pane-hooks';
import {ChartDataWindow, studyDataRows} from './data-window';
import {runDrawingScript} from './drawing-scripts';
import {pythonState} from './python-state';
import {studyName, useStudyCatalog} from './study-catalog';
import {DrawingStyleBar} from './drawing-style-bar';
import {
  displayTickSize,
  formatAge,
  formatInstrumentPrice,
  formatUtcTime,
  formatVolume,
} from './format';
import {chartFeedState} from './feed-status';
import {MarketStatusBadge, useBarCalendar} from './market-status';
import {isRateInstrument, venueAccess} from './markets';
import {useVenueCapabilities} from './venue-capabilities';
import {ProvenanceBadge, ProvenanceRibbon} from './provenance';
import {useStore} from './react-bindings';
import {ReplayBar} from './replay-bar';
import {useStrategyPreview} from './strategy-preview';

/** Bars requested on load. */
const HISTORY_BARS = 500;
/** Bars shown when a chart opens without a saved viewport. */
const INITIAL_VISIBLE_BARS = 140;
/** Feed modes offered per chart (REPLAY is entered with bar replay). */
const FEED_MODES: ReadonlyArray<[FeedMode, string]> = [
  ['AUTO', 'Auto feed'],
  ['NATIVE_WS', 'WebSocket'],
  ['REST_POLL', 'REST polling'],
];
/** Link groups cycled from the pane header. */
const LINK_GROUPS: ReadonlyArray<ChartState['link_group']> = [
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
  /** Provenance of what is displayed, as the engine reported it. */
  feedState: FeedState;
  /** Actionable error, or null. */
  error: string | null;
  /** When the latest data arrived (UTC ms), or null before any. */
  lastUpdateMs: number | null;
}

/** Context-menu request. */
export interface PaneMenu {
  /** X in CSS pixels, relative to the plot area of the chart canvas. */
  x: number;
  /** Y in CSS pixels, relative to the chart canvas. */
  y: number;
  /** Price under the pointer, or null when the chart cannot map it. */
  price: number | null;
  /** Time under the pointer (UTC ms), or null when the chart cannot map it. */
  timeMs: number | null;
}

/**
 * Converts a contract candle to a render bar.
 * @param candle Contract candle.
 * @return Bar with display numbers only.
 */
export function toChartBar(candle: Candle): ChartBar {
  return candleToBar(candle);
}

/**
 * Reads a CSS custom property from the document root.
 * @param name Property name, e.g. `--chart-bg`.
 * @return Computed value; empty when the property is not set.
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
  /** Opens the context menu (null closes it). */
  onMenu: (chartId: string, menu: PaneMenu | null) => void;
  /** Extra header buttons (e.g. open in another window). */
  headerActions?: ReactNode;
}

/**
 * Renders one chart pane.
 * @param props Props.
 * @return Pane element.
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
  const [priceAdjustment, setPriceAdjustment] =
    useState<PriceAdjustment | null>(null);
  const [hover, setHover] = useState<ChartBar | null>(null);
  const [lastBar, setLastBar] = useState<ChartBar | null>(null);
  const [now, setNow] = useState(() => Date.now());
  // Refs carry the latest values into chart and stream callbacks without
  // re-subscribing them on every render.
  const barsRef = useRef<ChartBar[]>([]);
  const loadEarlierRef = useRef<((fromMs: number) => void) | null>(null);
  // Bars actually displayed (truncated during replay).
  const shownRef = useRef<readonly ChartBar[]>([]);
  const [barCount, setBarCount] = useState(0);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const chartRef = useRef(chart);
  chartRef.current = chart;
  const workspace = useStore(runtime.workspace);
  const theme = useStore(runtime.theme);
  const dataWindow = useStore(runtime.dataWindow);
  const replayStates = useStore(runtime.replay);
  const {
    chart_id: chartId,
    instrument_id: instrumentId,
    timeframe,
    feed_mode: feedMode,
  } = chart;
  const replay: ReplayState | null = replayStates[chartId] ?? null;
  const replayRef = useRef(replay);
  replayRef.current = replay;
  // Session calendar of the instrument: the registry's `calendar` once
  // loaded, else the calendar implied by the id (24x7 for crypto).
  const calendarId = instrumentCalendarId(instrumentId, instrument);
  const calendar = useBarCalendar(runtime, calendarId);
  const calendarRef = useRef(calendar);
  calendarRef.current = calendar;
  const calendarIdRef = useRef(calendarId);
  calendarIdRef.current = calendarId;
  const rate = isRateInstrument(instrumentId);
  // Calendar-aware: a monthly cursor bar closes on the 1st of next month,
  // a session's last bar at the close.
  const atMs = replayAsOfMs(replay, timeframe, calendar);
  const atMsRef = useRef(atMs);
  atMsRef.current = atMs;
  const tickSize = instrument?.tick_size ?? null;
  // Instrument metadata usually arrives after the drawing controller is
  // built; the controller reads the tick size through this ref.
  const tickSizeRef = useRef(tickSize);
  tickSizeRef.current = tickSize;
  // Ladder markets (TWSE, TPEx) format the axis with the finest tick that can
  // apply within a 10% day band around the latest price.
  const reference = lastBar?.close ?? null;
  const axisTick = useMemo(() => {
    if (!instrument) return tickSize ?? '0.01';
    if (reference === null) return instrument.tick_size;
    return displayTickSize(instrument, reference * 0.9, reference * 1.1);
  }, [instrument, tickSize, reference]);

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
      if (created && runtime.charts.get(chartId) === created) {
        runtime.charts.delete(chartId);
      }
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
        if (alive) {
          setInstrument(
            list.instruments.find(i => i.instrument_id === instrumentId) ?? null
          );
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [instrumentId, runtime]);

  // Series presentation. Yields are plotted in percent without volume.
  useEffect(() => {
    port?.setSeries({
      instrumentId,
      timeframe,
      chartType: chart.chart_type,
      priceScale: chart.price_scale,
      tickSize: axisTick,
      showVolume: chart.show_volume && !rate,
      calendar,
      ...(rate ? {priceUnit: '%'} : {}),
    });
  }, [
    port,
    instrumentId,
    timeframe,
    chart.chart_type,
    chart.price_scale,
    chart.show_volume,
    axisTick,
    calendar,
    rate,
  ]);

  // A yield opens as a line chart; leaving it restores candles if the line
  // was chosen automatically.
  const autoLine = useRef<string | null>(null);
  const marketType = instrument?.key.market_type ?? null;
  useEffect(() => {
    if (marketType === null) return;
    const type = chartRef.current.chart_type;
    if (marketType === 'rate' && type !== 'line' && type !== 'area') {
      autoLine.current = instrumentId;
      runtime.dispatch({type: 'setChartType', chartId, chartType: 'line'});
    } else if (marketType !== 'rate' && autoLine.current !== null) {
      autoLine.current = null;
      if (type === 'line') {
        runtime.dispatch({type: 'setChartType', chartId, chartType: 'candles'});
      }
    }
  }, [marketType, instrumentId, chartId, runtime]);

  // Better session data (engine list loaded) changes bar boundaries of
  // engine-backed drawings and the off-data axis: rebuild and repaint.
  useEffect(() => {
    runtime.drawing.series.get(chartId)?.reconcile();
    port?.invalidate();
  }, [calendar, port, runtime, chartId]);

  // History and stream. A backend failure never falls back to synthetic data.
  useEffect(() => {
    if (!port) return;
    let alive = true;
    const historyController = new AbortController();
    let loadingHistory = false;
    let loadingEarlier = false;
    let exhausted = false;
    let retryAfterMs = 0;
    let priceBasis: string | null = null;
    let basisRefreshAfter: number | undefined;
    const snapshotUpdates = new Map<number, ChartBar>();
    setPriceAdjustment(null);
    setStatus({
      phase: 'loading',
      feedState: 'CONNECTING',
      error: null,
      lastUpdateMs: null,
    });
    barsRef.current = [];
    shownRef.current = [];
    setBarCount(0);
    port.setBars([]);
    /** Loads the next page when the viewport reaches the first loaded bars. */
    const loadEarlier = (fromMs: number): void => {
      const bars = barsRef.current;
      const oldest = bars[0]?.timeMs;
      if (
        oldest === undefined ||
        loadingHistory ||
        loadingEarlier ||
        exhausted ||
        replayRef.current
      ) {
        return;
      }
      const threshold = calendarRef.current.shift(timeframe, oldest, 50);
      if (fromMs > threshold || Date.now() < retryAfterMs) return;
      loadingEarlier = true;
      void runtime.clients.market
        .getCandles({
          instrumentId,
          timeframe,
          toMs: oldest,
          limit: HISTORY_BARS,
          signal: historyController.signal,
        })
        .then(batch => {
          if (!alive) return;
          const incomingBasis = `${batch.price_adjustment ?? 'unknown'}:${batch.price_adjustment_epoch_ms ?? ''}`;
          if (priceBasis !== incomingBasis) {
            // Corporate actions can rebase every previously loaded page.
            setLoadAttempt(attempt => attempt + 1);
            return;
          }
          const earlier = batch.candles
            .map(toChartBar)
            .filter(bar => bar.timeMs < oldest);
          if (earlier.length === 0) {
            exhausted = true;
            return;
          }
          const visible = port.serialize().visibleRange;
          const merged = new Map<number, ChartBar>();
          for (const bar of [...earlier, ...barsRef.current]) {
            merged.set(bar.timeMs, bar);
          }
          barsRef.current = [...merged.values()].sort(
            (a, b) => a.timeMs - b.timeMs
          );
          shownRef.current = barsRef.current;
          setBarCount(barsRef.current.length);
          port.setBars(barsRef.current);
          if (visible) port.setVisibleRange(visible);
        })
        .catch(() => {
          if (alive) retryAfterMs = Date.now() + 5_000;
        })
        .finally(() => {
          loadingEarlier = false;
        });
    };
    loadEarlierRef.current = loadEarlier;
    setLastBar(null);
    setHover(null);
    let batchState: FeedState = 'CONNECTING';
    let liveState: FeedState | null = null;
    /** Fetches one snapshot at a time, preserving any newer socket bars. */
    const load = (): void => {
      if (loadingHistory || !alive) return;
      loadingHistory = true;
      snapshotUpdates.clear();
      void runtime.clients.market
        .getCandles({
          instrumentId,
          timeframe,
          limit: HISTORY_BARS,
          signal: historyController.signal,
        })
        .then(batch => {
          if (!alive) return;
          const incomingBasis = `${batch.price_adjustment ?? 'unknown'}:${batch.price_adjustment_epoch_ms ?? ''}`;
          // Older pages survive refreshes only while their price basis agrees.
          const cached = priceBasis === incomingBasis ? barsRef.current : [];
          const merged = new Map(cached.map(bar => [bar.timeMs, bar]));
          for (const candle of batch.candles) {
            const bar = toChartBar(candle);
            merged.set(bar.timeMs, bar);
          }
          for (const bar of snapshotUpdates.values()) {
            merged.set(bar.timeMs, bar);
          }
          priceBasis = incomingBasis;
          basisRefreshAfter = batch.price_adjustment_refresh_after_ms;
          setPriceAdjustment(batch.price_adjustment ?? null);
          const bars = [...merged.values()].sort((a, b) => a.timeMs - b.timeMs);
          barsRef.current = bars;
          const shown = replayVisibleBars(bars, replayRef.current);
          shownRef.current = shown;
          setBarCount(bars.length);
          port.setBars(shown);
          const saved = chartRef.current.visible_range;
          const first = bars[Math.max(0, bars.length - INITIAL_VISIBLE_BARS)];
          const lastStart = bars.at(-1)?.timeMs;
          // Sessions covering the loaded bars (and a margin) from the engine.
          if (bars[0] && lastStart !== undefined) {
            runtime.calendars.prefetch(
              calendarIdRef.current,
              bars[0].timeMs,
              lastStart
            );
          }
          if (saved) {
            port.setVisibleRange({fromMs: saved.from_ms, toMs: saved.to_ms});
          } else if (first && lastStart !== undefined) {
            port.setVisibleRange({
              fromMs: first.timeMs,
              toMs: calendarRef.current.shift(timeframe, lastStart, 6),
            });
          } else {
            port.fitContent();
          }
          setLastBar(bars.at(-1) ?? null);
          batchState = batch.feed_state;
          setStatus({
            phase: bars.length ? 'ready' : 'empty',
            feedState: liveState ?? batch.feed_state,
            error: null,
            lastUpdateMs: Date.now(),
          });
        })
        .catch((error: unknown) => {
          if (!alive) return;
          setStatus(previous => ({
            ...previous,
            phase: barsRef.current.length ? 'ready' : 'error',
            feedState: liveState ?? 'UNAVAILABLE',
            error: error instanceof Error ? error.message : 'Request failed',
          }));
        })
        .finally(() => {
          loadingHistory = false;
        });
    };
    void load();
    const unsubscribe = runtime.clients.marketStream.subscribe(
      [
        {
          channel: 'candles',
          instrument_id: instrumentId,
          timeframe,
          feed_mode: feedMode,
        },
      ],
      envelope => {
        if (!alive) return;
        if (envelope.type === 'snapshot_required') {
          void load();
          return;
        }
        if (envelope.type !== 'candle.updated') return;
        // The stream contract types payloads loosely; a `candle.updated`
        // payload is a candle, and it is checked against this chart below.
        const candle = envelope.payload as unknown as Candle;
        if (
          candle.instrument_id !== instrumentId ||
          candle.timeframe !== timeframe
        ) {
          return;
        }
        liveState = candle.feed_state;
        const bar = toChartBar(candle);
        if (loadingHistory) snapshotUpdates.set(bar.timeMs, bar);
        const bars = barsRef.current;
        const last = bars.at(-1);
        if (last && bar.timeMs < last.timeMs) return;
        if (last && bar.timeMs === last.timeMs) bars[bars.length - 1] = bar;
        else bars.push(bar);
        // A closed bar adds a point to engine-backed drawings (VWAP...).
        if (bar.final || (last && bar.timeMs > last.timeMs)) {
          runtime.drawing.series.get(chartId)?.refresh();
        }
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
    // Adjusted history may change after the next corporate-action session.
    const basisTimer = setInterval(() => {
      if (basisRefreshAfter !== undefined && Date.now() >= basisRefreshAfter) {
        load();
      }
    }, 30_000);
    return () => {
      alive = false;
      clearInterval(basisTimer);
      historyController.abort();
      loadEarlierRef.current = null;
      unsubscribe();
      offStatus();
    };
  }, [port, instrumentId, timeframe, feedMode, runtime, chartId, loadAttempt]);

  // Staleness clock (also drives the bar countdown and market status).
  useEffect(() => {
    const timer = setInterval(() => void setNow(Date.now()), 1000);
    return () => void clearInterval(timer);
  }, []);
  const ageMs = status.lastUpdateMs === null ? 0 : now - status.lastUpdateMs;
  const effectiveState = chartFeedState({
    feedState: status.feedState,
    calendar,
    nowMs: now,
    lastUpdateMs: status.lastUpdateMs,
    ready: status.phase === 'ready',
    replaying: replay !== null,
  });
  useEffect(() => {
    runtime.feedStates.update(states =>
      states[chartId] === effectiveState
        ? states
        : {...states, [chartId]: effectiveState}
    );
  }, [runtime, chartId, effectiveState]);
  useEffect(
    () => () =>
      void runtime.feedStates.update(states => {
        const {[chartId]: _removed, ...rest} = states;
        return rest;
      }),
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
    // Engine-backed drawings run their built-in scripts on the engine.
    const series = new DrawingSeriesSync(store, runtime.tools, {
      fetch: query => runDrawingScript(runtime, query),
      instrumentId: () => instrumentId,
      timeframe: () => timeframe,
      calendar: () => calendarRef.current,
      onChange: () => {
        port.invalidate();
        runtime.drawing.revision.update(n => n + 1);
      },
    });
    const controller = new DrawingController(port, store, runtime.tools, {
      instrumentId: () => instrumentId,
      timeframe: () => timeframe,
      tickSize: () => tickSizeRef.current,
      bars: () => shownRef.current,
      durationMs: () => TIMEFRAME_MS[timeframe],
      calendar: () => calendarRef.current,
      magnet: () => runtime.drawing.magnet.get(),
      palette,
      now: () => Date.now(),
      newId: ids.next,
      series: doc => seriesAsOf(series.get(doc.drawing_id), atMsRef.current),
      onToolChange: tool => {
        if (runtime.selection.get().chartId === chartId) {
          runtime.drawing.activeTool.set(tool);
        }
      },
      onSelectionChange: drawingId => {
        const current = runtime.drawing.selection.get();
        if (drawingId) runtime.drawing.selection.set({chartId, drawingId});
        else if (current?.chartId === chartId) {
          runtime.drawing.selection.set(null);
        }
      },
    });
    const offAutosave = bindAutosave(store, repository, instrumentId);
    const offStore = store.subscribe(
      () => void runtime.drawing.revision.update(n => n + 1)
    );
    runtime.drawing.controllers.set(chartId, controller);
    runtime.drawing.stores.set(chartId, store);
    runtime.drawing.series.set(chartId, series);
    const armed = runtime.drawing.activeTool.get();
    if (armed && runtime.selection.get().chartId === chartId) {
      controller.setTool(armed);
    }
    runtime.drawing.revision.update(n => n + 1);
    return () => {
      offStore();
      offAutosave();
      if (runtime.drawing.selection.get()?.chartId === chartId) {
        runtime.drawing.selection.set(null);
      }
      controller.dispose();
      series.dispose();
      if (runtime.drawing.series.get(chartId) === series) {
        runtime.drawing.series.delete(chartId);
      }
      if (runtime.drawing.controllers.get(chartId) === controller) {
        runtime.drawing.controllers.delete(chartId);
      }
      if (runtime.drawing.stores.get(chartId) === store) {
        runtime.drawing.stores.delete(chartId);
      }
    };
  }, [port, instrumentId, timeframe, workspace.workspace_id, chartId, runtime]);

  // Feature overlay layers, shown when their provider marks them visible.
  const layerVisibility = useMemo(() => new Map<string, boolean>(), []);
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
    const subscriptions: Array<() => void> = [];
    const attach = () => {
      for (const off of subscriptions.splice(0)) {
        off();
      }
      for (const id of applied) {
        port.removeOverlayLayer(id);
      }
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
      for (const off of subscriptions.splice(0)) {
        off();
      }
      for (const id of applied) {
        port.removeOverlayLayer(id);
      }
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
      if (
        sourceChartId === chartId ||
        !runtime.workspace.get().sync.crosshair
      ) {
        return;
      }
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
    // Scrolling and zooming are saved 600 ms after they stop, unless the
    // range is still the saved one (within 1 s at both ends).
    let rangeTimer: ReturnType<typeof setTimeout> | null = null;
    const offRange = port.on('visibleRangeChange', range => {
      if (range) loadEarlierRef.current?.(range.fromMs);
      if (rangeTimer) clearTimeout(rangeTimer);
      rangeTimer = setTimeout(() => {
        if (!range) return;
        // Keep sessions loaded around what the user scrolls to.
        runtime.calendars.prefetch(
          calendarIdRef.current,
          range.fromMs,
          range.toMs
        );
        const saved = chartRef.current.visible_range;
        if (
          saved &&
          Math.abs(saved.from_ms - range.fromMs) < 1000 &&
          Math.abs(saved.to_ms - range.toMs) < 1000
        ) {
          return;
        }
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
    ) {
      return;
    }
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
      void runtime.replay.update(all => {
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
    return () => void clearInterval(timer);
  }, [replayPhase, replaySpeed, updateReplay]);
  // Leaving the instrument or timeframe ends replay on this chart.
  useEffect(
    () => () =>
      void runtime.replay.update(all => {
        const {[chartId]: _ended, ...rest} = all;
        return rest;
      }),
    [runtime, chartId, instrumentId, timeframe]
  );

  const symbolOf = useCallback(
    (id: string) => parseInstrumentId(id).venueMarketId,
    []
  );
  const studyErrors = useStudyRuns(
    port,
    chart,
    runtime,
    shownRef.current.filter(bar => bar.final).at(-1)?.timeMs ?? null,
    atMs
  );
  useStrategyPreview(port, chart, runtime);
  const studyResults = useStore(pythonState(runtime).results);
  const catalog = useStudyCatalog();
  const compare = useCompare(port, chart, runtime, symbolOf);
  useSessionSeparators(
    port,
    chart.session_separators === true,
    shownRef,
    timeframe,
    calendar.continuous ? 'UTC' : calendar.timeZone
  );
  // Between sessions the status badge shows the next open instead.
  const countdown = barCountdown(timeframe, now, calendar);
  const access = venueAccess(
    useVenueCapabilities(runtime, parseInstrumentId(instrumentId).venue)
  );
  const market =
    instrument?.listing_exchange ??
    (isExchangeCalendar(calendarId) ? EXCHANGE_HOURS[calendarId].market : '');
  const currency = rate ? null : (instrument?.key.quote ?? null);

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
          {priceAdjustment && (
            <span
              className="pane__chip"
              data-testid="price-adjustment"
              title={
                priceAdjustment === 'ALL'
                  ? 'Provider-adjusted historical OHLC for splits, dividends and other corporate actions. Volume stays as reported. History can change after later actions; this is not point-in-time data.'
                  : priceAdjustment === 'SPLIT'
                    ? 'Historical OHLC normalized for stock splits using provider data. Current quotes and order prices remain native.'
                    : 'Unadjusted prices. Corporate actions may create visible price gaps.'
              }
            >
              {priceAdjustment === 'ALL'
                ? 'Adjusted · splits & dividends'
                : priceAdjustment === 'SPLIT'
                  ? 'Split adjusted'
                  : 'Raw prices'}
            </span>
          )}
          {instrument?.listing_exchange && (
            <span
              className="pane__chip"
              title={`Listed on ${instrument.listing_exchange}${currency ? `, quoted in ${currency}` : ''}`}
              data-testid="listing-exchange"
            >
              {instrument.listing_exchange}
              {currency ? ` · ${currency}` : ''}
            </span>
          )}
          {access.delay && (
            <span
              className="pane__chip pane__chip--warning"
              data-testid="data-delay"
              title={`${access.delay}: this deployment receives delayed data for this venue`}
            >
              {access.delay}
            </span>
          )}
          {access.missingCredentials && (
            <span
              className="pane__chip pane__chip--danger"
              data-testid="missing-credentials"
              title={access.missingCredentials}
            >
              No API key
            </span>
          )}
          {rate && (
            <span className="pane__chip" title="Published yield in percent">
              Yield %
            </span>
          )}
          <span className="pane__chip" title={timeframeTitle(timeframe)}>
            {timeframe}
          </span>
          {!replay &&
            status.phase === 'ready' &&
            countdown.kind === 'closes' && (
              <span
                className="pane__chip pane__countdown j-tabular"
                title={
                  calendar.continuous
                    ? 'Time until this bar closes (UTC calendar)'
                    : `Time until this bar closes (${market} session calendar)`
                }
                data-testid="bar-countdown"
              >
                {formatCountdown(countdown.ms)}
              </span>
            )}
          {!calendar.continuous && (
            <MarketStatusBadge
              calendar={calendar}
              market={market}
              nowMs={now}
            />
          )}
          <button
            type="button"
            className="pane__chip pane__chip--button"
            data-testid="link-group"
            title="Link group: linked charts follow each other when sync is on"
            aria-label={`${chart.link_group ? `Link ${chart.link_group}` : 'Unlinked'}: change link group`}
            onClick={() =>
              void runtime.dispatch({
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
            // The options are exactly `FEED_MODES`.
            onChange={event =>
              void runtime.dispatch({
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
                  void runtime.dispatch({
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
          aria-label={
            rate
              ? 'Open, high, low, close'
              : 'Open, high, low, close and volume'
          }
        >
          {(['open', 'high', 'low', 'close'] as const).map(key => (
            <div key={key}>
              <dt>{key.charAt(0).toUpperCase()}</dt>
              <dd className={key === 'close' ? direction : undefined}>
                {shown
                  ? formatInstrumentPrice(String(shown[key]), instrument)
                  : '—'}
              </dd>
            </div>
          ))}
          {!rate && (
            <div
              data-testid="bar-volume"
              title="Volume of the selected bar; follows the crosshair and replay cursor"
            >
              <dt>V</dt>
              <dd aria-label="Bar volume">
                {formatVolume(shown?.volume, instrument)}
              </dd>
            </div>
          )}
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
      <StudyLegend chart={chart} errors={studyErrors} />
      {dataWindow && status.phase === 'ready' && (
        <ChartDataWindow
          runtime={runtime}
          chartId={chartId}
          atMs={atMs}
          bar={shown}
          timeframe={timeframe}
          calendar={calendar}
          instrument={instrument}
          previousClose={previousClose}
          tickSize={tickSize}
          studyRows={
            shown
              ? studyDataRows(
                  chart,
                  studyResults,
                  shown.timeMs,
                  id => {
                    const study = chart.studies.find(s => s.study_id === id);
                    return study ? studyName(catalog, study) : id;
                  },
                  atMs,
                  instrument
                    ? value => formatVolume(value, instrument)
                    : undefined
                )
              : []
          }
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
            void updateReplay(state =>
              stepReplay(
                state,
                barsRef.current.map(bar => bar.timeMs),
                step
              )
            )
          }
          onPlayPause={() =>
            void updateReplay(state => ({
              ...state,
              phase: state.phase === 'playing' ? 'paused' : 'playing',
            }))
          }
          onSpeed={() => void updateReplay(nextReplaySpeed)}
          onStartDefault={() =>
            void updateReplay(state =>
              chooseReplayStart(
                state,
                barsRef.current.map(bar => bar.timeMs),
                null
              )
            )
          }
          onExit={() =>
            void runtime.replay.update(all => {
              const {[chartId]: _ended, ...rest} = all;
              return rest;
            })
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
            <p>Check the market engine connection, then retry.</p>
            <button
              className="j-btn"
              onClick={() => void setLoadAttempt(n => n + 1)}
            >
              Retry candles
            </button>
          </div>
        </div>
      )}
      {status.phase === 'ready' && status.error && (
        <div className="pane__stale j-notice j-notice--danger" role="alert">
          History request failed: {status.error}{' '}
          <button
            className="j-btn"
            onClick={() => void setLoadAttempt(n => n + 1)}
          >
            Retry candles
          </button>
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
