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
  type Candle,
  type ChartState,
  type FeedState,
  type Instrument,
  type StreamStatus,
} from '@joseph/contracts';
import {
  DrawingController,
  DrawingRepository,
  DrawingStore,
  bindAutosave,
} from '@joseph/drawing-core';
import {ids} from './ids';
import {useEffect, useMemo, useRef, useState, type ReactElement} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {formatAge, formatPrice, formatUtcTime} from './format';
import {ProvenanceBadge, ProvenanceRibbon} from './provenance';
import {useStore} from './react-bindings';

/** Bars requested on load. */
const HISTORY_BARS = 500;
/** Bars shown when a chart opens without a saved viewport. */
const INITIAL_VISIBLE_BARS = 140;
/** Age after which a live feed counts as stale. */
const STALE_AFTER_MS = 60_000;

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
  return {
    timeMs: candle.start_ms,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
    volume: Number(candle.base_volume),
    final: candle.is_final,
  };
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
  const chartRef = useRef(chart);
  chartRef.current = chart;
  const workspace = useStore(runtime.workspace);
  const theme = useStore(runtime.theme);
  const {chart_id: chartId, instrument_id: instrumentId, timeframe} = chart;
  const tickSize = instrument?.tick_size ?? null;

  // Mount the chart library lazily on the client.
  useEffect(() => {
    let disposed = false;
    let created: ChartPort | null = null;
    void import('@joseph/chart-core/lwc').then(({createLwcChartPort}) => {
      if (disposed || !hostRef.current) return;
      created = createLwcChartPort();
      created.mount(hostRef.current, themeFromCss(cssVar));
      setPort(created);
    });
    return () => {
      disposed = true;
      created?.destroy();
      setPort(null);
    };
    // Theme changes remount so canvas colors follow the tokens.
  }, [theme]);

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
          port.setBars(bars);
          const saved = chartRef.current.visible_range;
          const duration = TIMEFRAME_MS[timeframe];
          const first = bars[Math.max(0, bars.length - INITIAL_VISIBLE_BARS)];
          const lastStart = bars.at(-1)?.timeMs;
          if (saved) {
            port.setVisibleRange({fromMs: saved.from_ms, toMs: saved.to_ms});
          } else if (first && lastStart !== undefined) {
            port.setVisibleRange({
              fromMs: first.timeMs,
              toMs: lastStart + 6 * duration,
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
  }, [port, instrumentId, timeframe, runtime]);

  // Staleness clock.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const ageMs = status.lastUpdateMs === null ? 0 : now - status.lastUpdateMs;
  const effectiveState: FeedState =
    status.feedState !== 'DEMO' &&
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
      };
    };
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
      onToolChange: tool => {
        if (runtime.selection.get().chartId === chartId)
          runtime.drawing.activeTool.set(tool);
      },
    });
    const offAutosave = bindAutosave(store, repository, instrumentId);
    const offStore = store.subscribe(() =>
      runtime.drawing.revision.update(n => n + 1)
    );
    runtime.drawing.controllers.set(chartId, controller);
    runtime.drawing.stores.set(chartId, store);
    const armed = runtime.drawing.activeTool.get();
    if (armed && runtime.selection.get().chartId === chartId)
      controller.setTool(armed);
    runtime.drawing.revision.update(n => n + 1);
    return () => {
      offStore();
      offAutosave();
      controller.dispose();
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
        const store = provider({chartId, instrumentId, timeframe});
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
  }, [port, chartId, instrumentId, timeframe, runtime]);
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
      runtime.events.emit('chart.overlayDragEnd', {chartId, ...event});
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

  const shown = hover ?? lastBar;
  const direction = shown && shown.close >= shown.open ? 'j-pos' : 'j-neg';
  const {venue, venueMarketId} = parseInstrumentId(instrumentId);
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
          <span className="pane__chip">{timeframe}</span>
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
          <ProvenanceBadge state={effectiveState} ageMs={ageMs} />
          {status.lastUpdateMs !== null && (
            <span className="pane__asof j-tabular">
              as of {formatUtcTime(status.lastUpdateMs, true)}
            </span>
          )}
        </div>
      </header>
      <div className="pane__canvas" ref={hostRef} data-testid="chart-canvas" />
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
