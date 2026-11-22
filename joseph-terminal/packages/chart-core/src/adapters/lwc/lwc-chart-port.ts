/**
 * @fileoverview Lightweight Charts (Apache-2.0) implementation of `ChartPort`.
 *
 * This is the only module that imports `lightweight-charts`. It uses public
 * APIs only: series, price lines, the markers plugin and series primitives.
 */
import {
  AreaSeries,
  BarSeries,
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type ISeriesPrimitive,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type LogicalRange,
  type MouseEventParams,
  type SeriesAttachedParameter,
  type SeriesMarker,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import type {
  CanvasLayer,
  ChartBar,
  ChartEventMap,
  ChartEventName,
  ChartPointerEvent,
  ChartPort,
  ChartTheme,
  ChartViewState,
  CompareSeriesSpec,
  CoordinateTransform,
  Disposer,
  SeriesConfig,
  StudyPaneSpec,
  TimeRange,
} from '../../chart-port';
import type {
  MarkerOverlay,
  OverlayLayer,
  OverlayLineStyle,
  PriceLineOverlay,
} from '../../overlay-model';
import {paintOverlays} from '../../overlay-painter';
import {DEFAULT_CHART_THEME, toneColor, withAlpha} from '../../theme';
import {TimeIndex} from '../../time-index';
import {heikinAshiBar, toHeikinAshi} from '../../transforms/heikin-ashi';
import {TIMEFRAME_MS, stepDecimals} from '@joseph/contracts';

/** Pixel tolerance for grabbing a draggable price line. */
const DRAG_TOLERANCE_PX = 6;
/** Movement (px) after which a press is a drag rather than a click. */
const CLICK_SLOP_PX = 4;
/** Share of the chart height given to the volume pane. */
const VOLUME_PANE_SHARE = 0.14;
/** Share of the chart height given to each study pane. */
const STUDY_PANE_SHARE = 0.16;
/** Smallest share kept for the price pane. */
const MIN_PRICE_PANE_SHARE = 0.45;

/** Native objects backing one study pane. */
interface StudyState {
  /** Latest spec. */
  spec: StudyPaneSpec;
  /** Series in the study's pane. */
  series: ISeriesApi<'Histogram'> | ISeriesApi<'Line'> | null;
  /** Caption primitive attached to the series. */
  primitive: HostPrimitive | null;
  /** Shape the series was created with. */
  style: StudyPaneSpec['style'] | null;
}

/**
 * Converts milliseconds to an LWC UTC timestamp (seconds).
 * @param timeMs UTC ms.
 * @return LWC time.
 */
function toTime(timeMs: number): UTCTimestamp {
  return Math.floor(timeMs / 1000) as UTCTimestamp;
}

/**
 * Maps a line style.
 * @param style Overlay line style.
 * @return LWC line style.
 */
function lineStyleOf(style: OverlayLineStyle): LineStyle {
  if (style === 'dashed') return LineStyle.Dashed;
  if (style === 'dotted') return LineStyle.Dotted;
  return LineStyle.Solid;
}

/**
 * Maps a marker model to an LWC marker.
 * @param marker Marker model.
 * @param theme Theme.
 * @param snap Snaps a time to its bar start.
 * @return LWC marker.
 */
export function toLwcMarker(
  marker: MarkerOverlay,
  theme: ChartTheme,
  snap: (timeMs: number) => number | null
): SeriesMarker<Time> {
  const position =
    marker.position === 'above'
      ? 'aboveBar'
      : marker.position === 'below'
        ? 'belowBar'
        : 'inBar';
  const shape =
    marker.shape === 'arrow-up'
      ? 'arrowUp'
      : marker.shape === 'arrow-down'
        ? 'arrowDown'
        : marker.shape;
  return {
    time: toTime(snap(marker.timeMs) ?? marker.timeMs),
    position,
    shape,
    color: marker.color ?? toneColor(theme, marker.tone),
    text: marker.text ?? '',
  };
}

/** Pane view that delegates painting to the port. */
class PaintView implements IPrimitivePaneView {
  /**
   * Creates the view.
   * @param paint Paint callback in CSS pixels.
   */
  constructor(
    private readonly paint: (ctx: CanvasRenderingContext2D) => void
  ) {}

  /**
   * Draws above series.
   * @return Z order.
   */
  zOrder(): 'top' {
    return 'top';
  }

  /**
   * Returns the renderer.
   * @return Renderer.
   */
  renderer(): IPrimitivePaneRenderer {
    return {
      draw: target => {
        target.useMediaCoordinateSpace(({context}) => {
          this.paint(context);
        });
      },
    };
  }
}

/** Series primitive hosting overlay boxes and custom canvas layers. */
class HostPrimitive implements ISeriesPrimitive<Time> {
  private requestUpdate: (() => void) | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  /**
   * Creates the primitive.
   * @param paint Paint callback.
   */
  constructor(paint: (ctx: CanvasRenderingContext2D) => void) {
    this.views = [new PaintView(paint)];
  }

  /**
   * Stores the update hook.
   * @param param Attachment parameters.
   */
  attached(param: SeriesAttachedParameter<Time>): void {
    this.requestUpdate = param.requestUpdate;
  }

  /** Drops the update hook. */
  detached(): void {
    this.requestUpdate = null;
  }

  /**
   * Returns pane views.
   * @return Views.
   */
  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  /** Requests a repaint. */
  update(): void {
    this.requestUpdate?.();
  }
}

/** State of an in-progress price-line drag. */
interface DragState {
  /** Layer id. */
  layerId: string;
  /** Model id. */
  modelId: string;
  /** Native line being moved. */
  line: IPriceLine;
  /** Latest price. */
  price: number;
}

/** Lightweight Charts adapter. */
export class LwcChartPort implements ChartPort {
  private container: HTMLElement | null = null;
  private chart: IChartApi | null = null;
  private series: ISeriesApi<SeriesType> | null = null;
  private volume: ISeriesApi<'Histogram'> | null = null;
  private markers: ISeriesMarkersPluginApi<Time> | null = null;
  private primitive: HostPrimitive | null = null;
  private volumePrimitive: HostPrimitive | null = null;
  private readonly studies = new Map<string, StudyState>();
  private compareSpecs: readonly CompareSeriesSpec[] = [];
  private readonly compareSeries = new Map<string, ISeriesApi<'Line'>>();
  private theme: ChartTheme = DEFAULT_CHART_THEME;
  private config: SeriesConfig | null = null;
  private bars: ChartBar[] = [];
  private index = new TimeIndex([], 60_000);
  private readonly layers = new Map<string, OverlayLayer>();
  private readonly nativeLines = new Map<string, IPriceLine>();
  private readonly canvasLayers = new Map<string, CanvasLayer>();
  private readonly handlers = new Map<string, Set<(payload: never) => void>>();
  private readonly cleanups: Disposer[] = [];
  private drag: DragState | null = null;
  private press: {x: number; y: number; moved: boolean} | null = null;
  private interactive = true;
  private suppressCrosshair = false;
  private lastCrosshairNull = true;

  /**
   * Mounts the chart.
   * @param container Host element.
   * @param theme Theme.
   */
  mount(container: HTMLElement, theme: ChartTheme): void {
    if (this.chart) return;
    this.container = container;
    this.theme = theme;
    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: {type: ColorType.Solid, color: theme.background},
        textColor: theme.text,
        fontFamily: theme.fontFamily,
        fontSize: 11,
        attributionLogo: false,
        panes: {
          separatorColor: theme.border,
          separatorHoverColor: withAlpha(theme.crosshair, 0.35),
          enableResize: true,
        },
      },
      grid: {
        vertLines: {color: theme.grid},
        horzLines: {color: theme.grid},
      },
      rightPriceScale: {borderColor: theme.border},
      leftPriceScale: {
        borderColor: theme.border,
        visible: false,
        mode: PriceScaleMode.Percentage,
      },
      timeScale: {
        borderColor: theme.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {color: theme.crosshair, labelBackgroundColor: theme.border},
        horzLine: {color: theme.crosshair, labelBackgroundColor: theme.border},
      },
      localization: {locale: 'en-US'},
    });
    this.chart = chart;
    const onCrosshair = (param: MouseEventParams<Time>) =>
      this.handleCrosshair(param);
    chart.subscribeCrosshairMove(onCrosshair);
    this.cleanups.push(() => chart.unsubscribeCrosshairMove(onCrosshair));
    const onRange = (range: LogicalRange | null) => {
      this.emit(
        'visibleRangeChange',
        range ? this.logicalToRange(range) : null
      );
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
    this.cleanups.push(() =>
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange)
    );
    this.listen('pointerdown', event =>
      this.handlePointer('pointerDown', event)
    );
    this.listen('pointermove', event =>
      this.handlePointer('pointerMove', event)
    );
    this.listen('pointerup', event => this.handlePointer('pointerUp', event));
    this.listen('contextmenu', event => {
      event.preventDefault();
      this.emit('contextMenu', this.pointerEvent(event as PointerEvent, 2));
    });
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(entries => {
        const box = entries[0]?.contentRect;
        if (box) this.emit('resize', {width: box.width, height: box.height});
      });
      observer.observe(container);
      this.cleanups.push(() => observer.disconnect());
    }
    if (this.config) this.rebuildSeries();
  }

  /**
   * Adds a DOM listener in the capture phase and tracks its removal.
   * @param type Event type.
   * @param handler Handler.
   */
  private listen(type: string, handler: (event: Event) => void): void {
    const container = this.container;
    if (!container) return;
    container.addEventListener(type, handler, {capture: true});
    this.cleanups.push(() =>
      container.removeEventListener(type, handler, {capture: true})
    );
  }

  /**
   * Applies series configuration, rebuilding the series when needed.
   * @param config Series configuration.
   */
  setSeries(config: SeriesConfig): void {
    const previous = this.config;
    this.config = config;
    this.index = new TimeIndex(this.bars, TIMEFRAME_MS[config.timeframe]);
    const switched =
      previous !== null &&
      (previous.instrumentId !== config.instrumentId ||
        previous.timeframe !== config.timeframe);
    if (switched) {
      // Venue/instrument switches must never show the old venue's overlays,
      // studies or comparisons.
      this.bars = [];
      for (const id of [...this.layers.keys()]) this.removeOverlayLayer(id);
      this.removeAuxSeries();
      this.studies.clear();
      this.removeCompareSeries();
      this.compareSpecs = [];
    }
    if (!this.chart) return;
    if (!previous || previous.chartType !== config.chartType || !this.series) {
      this.rebuildSeries();
    } else {
      this.applySeriesOptions();
      if (switched || previous.showVolume !== config.showVolume) {
        this.rebuildAux();
        this.buildCompareSeries();
      }
      this.renderData();
    }
  }

  /** Recreates the main series for the current chart type. */
  private rebuildSeries(): void {
    const chart = this.chart;
    const config = this.config;
    if (!chart || !config) return;
    for (const line of this.nativeLines.values()) {
      this.series?.removePriceLine(line);
    }
    this.nativeLines.clear();
    this.markers?.detach();
    if (this.primitive) this.series?.detachPrimitive(this.primitive);
    this.removeAuxSeries();
    this.removeCompareSeries();
    if (this.series) chart.removeSeries(this.series);
    const theme = this.theme;
    const common = {priceLineVisible: true, lastValueVisible: true};
    switch (config.chartType) {
      case 'bars':
        this.series = chart.addSeries(BarSeries, {
          ...common,
          upColor: theme.bid,
          downColor: theme.ask,
        });
        break;
      case 'line':
        this.series = chart.addSeries(LineSeries, {
          ...common,
          color: theme.text,
          lineWidth: 2,
        });
        break;
      case 'area':
        this.series = chart.addSeries(AreaSeries, {
          ...common,
          lineColor: theme.text,
          topColor: 'rgba(143,165,179,0.28)',
          bottomColor: 'rgba(143,165,179,0.02)',
        });
        break;
      default:
        this.series = chart.addSeries(CandlestickSeries, {
          ...common,
          upColor: theme.bid,
          downColor: theme.ask,
          wickUpColor: theme.bid,
          wickDownColor: theme.ask,
          borderVisible: false,
        });
    }
    this.markers = createSeriesMarkers(this.series, []);
    this.primitive = new HostPrimitive(ctx => this.paint(ctx));
    this.series.attachPrimitive(this.primitive);
    this.applySeriesOptions();
    this.buildAuxSeries();
    this.buildCompareSeries();
    this.renderData();
    for (const layer of this.layers.values()) this.applyNativeLayer(layer);
    this.refreshMarkers();
  }

  /** Removes the volume and study series and their now-empty panes. */
  private removeAuxSeries(): void {
    const chart = this.chart;
    if (!chart) return;
    if (this.volume) {
      if (this.volumePrimitive)
        this.volume.detachPrimitive(this.volumePrimitive);
      chart.removeSeries(this.volume);
    }
    this.volume = null;
    this.volumePrimitive = null;
    for (const study of this.studies.values()) {
      if (study.series) {
        if (study.primitive) study.series.detachPrimitive(study.primitive);
        chart.removeSeries(study.series);
      }
      study.series = null;
      study.primitive = null;
      study.style = null;
    }
    const panes = chart.panes();
    for (let index = panes.length - 1; index > 0; index -= 1) {
      if ((panes[index]?.getSeries().length ?? 0) === 0)
        chart.removePane(index);
    }
  }

  /**
   * Creates the volume pane (when shown) and one pane per study, then sizes
   * the panes so the price pane keeps most of the height.
   */
  private buildAuxSeries(): void {
    const chart = this.chart;
    const config = this.config;
    if (!chart || !config) return;
    let pane = 1;
    if (config.showVolume) {
      this.volume = chart.addSeries(
        HistogramSeries,
        {
          priceFormat: {type: 'volume'},
          lastValueVisible: false,
          priceLineVisible: false,
        },
        pane
      );
      this.volume.priceScale().applyOptions({
        scaleMargins: {top: 0.12, bottom: 0},
        borderColor: this.theme.border,
      });
      this.volumePrimitive = new HostPrimitive(ctx =>
        this.paintPaneTitle(ctx, 'Volume', undefined)
      );
      this.volume.attachPrimitive(this.volumePrimitive);
      pane += 1;
    }
    for (const study of this.studies.values()) {
      this.createStudySeries(study, pane);
      pane += 1;
    }
    this.applyPaneSizes();
  }

  /**
   * Creates the native series of one study in a given pane.
   * @param study Study state.
   * @param pane Pane index.
   */
  private createStudySeries(study: StudyState, pane: number): void {
    const chart = this.chart;
    if (!chart) return;
    const precision = study.spec.precision ?? 2;
    const common = {
      priceFormat: {
        type: 'price' as const,
        precision,
        minMove: 1 / 10 ** precision,
      },
      lastValueVisible: true,
      priceLineVisible: false,
    };
    const series =
      study.spec.style === 'line'
        ? chart.addSeries(
            LineSeries,
            {...common, color: this.theme.text, lineWidth: 2},
            pane
          )
        : chart.addSeries(HistogramSeries, {...common}, pane);
    series.priceScale().applyOptions({
      scaleMargins: {top: 0.18, bottom: 0.08},
      borderColor: this.theme.border,
    });
    for (const level of study.spec.levels ?? []) {
      series.createPriceLine({
        price: level.value,
        color: withAlpha(toneColor(this.theme, level.tone), 0.8),
        lineWidth: 1,
        lineStyle: lineStyleOf(level.lineStyle),
        axisLabelVisible: true,
        title: level.label,
      });
    }
    const primitive = new HostPrimitive(ctx =>
      this.paintPaneTitle(ctx, study.spec.title, study.spec.caption)
    );
    series.attachPrimitive(primitive);
    study.series = series;
    study.primitive = primitive;
    study.style = study.spec.style;
  }

  /** Gives each auxiliary pane its share of the height. */
  private applyPaneSizes(): void {
    const panes = this.chart?.panes() ?? [];
    if (panes.length <= 1) return;
    const shares = panes
      .slice(1)
      .map((_, i) =>
        i === 0 && this.volume ? VOLUME_PANE_SHARE : STUDY_PANE_SHARE
      );
    const auxTotal = shares.reduce((sum, share) => sum + share, 0);
    const scale =
      1 - auxTotal < MIN_PRICE_PANE_SHARE
        ? (1 - MIN_PRICE_PANE_SHARE) / auxTotal
        : 1;
    panes[0]?.setStretchFactor(1 - auxTotal * scale);
    shares.forEach((share, i) => panes[i + 1]?.setStretchFactor(share * scale));
  }

  /**
   * Paints a pane title and optional provenance caption at the top-left.
   * @param ctx Context in CSS pixels.
   * @param title Title.
   * @param caption Optional caption.
   */
  private paintPaneTitle(
    ctx: CanvasRenderingContext2D,
    title: string,
    caption: string | undefined
  ): void {
    const text = caption ? `${title} · ${caption}` : title;
    ctx.save();
    ctx.font = `11px ${this.theme.fontFamily}`;
    ctx.textBaseline = 'middle';
    const width = ctx.measureText(text).width;
    ctx.fillStyle = withAlpha(this.theme.background, 0.85);
    ctx.fillRect(6, 4, width + 8, 16);
    ctx.fillStyle = this.theme.text;
    ctx.fillText(text, 10, 12);
    ctx.restore();
  }

  /** Removes every comparison series. */
  private removeCompareSeries(): void {
    for (const series of this.compareSeries.values()) {
      this.chart?.removeSeries(series);
    }
    this.compareSeries.clear();
  }

  /** Creates comparison series on the left percentage axis. */
  private buildCompareSeries(): void {
    const chart = this.chart;
    if (!chart) return;
    for (const spec of this.compareSpecs) {
      const series = chart.addSeries(
        LineSeries,
        {
          priceScaleId: 'left',
          color: spec.color,
          lineWidth: 2,
          title: spec.label,
          lastValueVisible: true,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
        },
        0
      );
      this.compareSeries.set(spec.id, series);
    }
    chart.applyOptions({
      leftPriceScale: {
        visible: this.compareSpecs.length > 0,
        mode: PriceScaleMode.Percentage,
        borderColor: this.theme.border,
      },
    });
  }

  /**
   * Adds or replaces a study pane.
   * @param spec Study spec.
   */
  setStudyPane(spec: StudyPaneSpec): void {
    const existing = this.studies.get(spec.id);
    if (
      existing &&
      existing.series &&
      existing.style === spec.style &&
      JSON.stringify(existing.spec.levels ?? []) ===
        JSON.stringify(spec.levels ?? [])
    ) {
      existing.spec = spec;
      this.renderStudy(existing);
      existing.primitive?.update();
      return;
    }
    this.studies.set(spec.id, {
      spec,
      series: null,
      primitive: null,
      style: null,
    });
    this.rebuildAux();
  }

  /**
   * Removes a study pane.
   * @param id Study id.
   */
  removeStudyPane(id: string): void {
    if (!this.studies.has(id)) return;
    this.removeAuxSeries();
    this.studies.delete(id);
    this.buildAuxSeries();
    this.renderAuxData();
  }

  /**
   * Replaces comparison overlays.
   * @param series Instruments to compare.
   */
  setCompareSeries(series: readonly CompareSeriesSpec[]): void {
    const sameShape =
      series.length === this.compareSpecs.length &&
      series.every(
        (spec, i) =>
          spec.id === this.compareSpecs[i]?.id &&
          spec.color === this.compareSpecs[i]?.color
      );
    this.compareSpecs = series;
    if (!this.chart || !this.series) return;
    if (!sameShape) {
      this.removeCompareSeries();
      this.buildCompareSeries();
    }
    this.renderCompare();
  }

  /** Rebuilds the volume and study panes (structure changed). */
  private rebuildAux(): void {
    if (!this.chart || !this.series) return;
    this.removeAuxSeries();
    this.buildAuxSeries();
    this.renderAuxData();
  }

  /**
   * Bar start times currently on the main series.
   * @return Set of times.
   */
  private barTimes(): Set<number> {
    return new Set(this.bars.map(bar => bar.timeMs));
  }

  /**
   * Pushes one study's points (only bars present on the main series).
   * @param study Study state.
   * @param times Main series bar times.
   */
  private renderStudy(study: StudyState, times = this.barTimes()): void {
    if (!study.series) return;
    const points = study.spec.points
      .filter(point => times.has(point.timeMs) && Number.isFinite(point.value))
      .sort((a, b) => a.timeMs - b.timeMs)
      .map(point => ({
        time: toTime(point.timeMs),
        value: point.value,
        ...(point.tone && study.spec.style === 'histogram'
          ? {color: withAlpha(toneColor(this.theme, point.tone), 0.7)}
          : {}),
      }));
    study.series.setData(points);
  }

  /** Pushes comparison closes (only bars present on the main series). */
  private renderCompare(): void {
    const times = this.barTimes();
    for (const spec of this.compareSpecs) {
      const series = this.compareSeries.get(spec.id);
      if (!series) continue;
      series.setData(
        spec.points
          .filter(p => times.has(p.timeMs) && Number.isFinite(p.value))
          .sort((a, b) => a.timeMs - b.timeMs)
          .map(p => ({time: toTime(p.timeMs), value: p.value}))
      );
    }
  }

  /** Pushes volume, study and comparison data. */
  private renderAuxData(): void {
    this.volume?.setData(this.bars.map(bar => this.toVolumePoint(bar)));
    const times = this.barTimes();
    for (const study of this.studies.values()) this.renderStudy(study, times);
    this.renderCompare();
  }

  /** Applies price format and scale mode. */
  private applySeriesOptions(): void {
    const config = this.config;
    if (!config || !this.series || !this.chart) return;
    const precision = Math.min(10, stepDecimals(config.tickSize));
    this.series.applyOptions({
      priceFormat: {type: 'price', precision, minMove: Number(config.tickSize)},
    });
    this.chart.applyOptions({
      localization: {
        locale: 'en-US',
        priceFormatter: (price: number) =>
          price.toLocaleString('en-US', {
            minimumFractionDigits: precision,
            maximumFractionDigits: precision,
          }),
      },
    });
    const mode =
      config.priceScale === 'log'
        ? PriceScaleMode.Logarithmic
        : config.priceScale === 'percent'
          ? PriceScaleMode.Percentage
          : PriceScaleMode.Normal;
    this.chart.priceScale('right').applyOptions({mode});
  }

  /**
   * Replaces bars.
   * @param bars Bars (any order, duplicates allowed).
   */
  setBars(bars: readonly ChartBar[]): void {
    const byTime = new Map<number, ChartBar>();
    for (const bar of bars) byTime.set(bar.timeMs, bar);
    this.bars = [...byTime.values()].sort((a, b) => a.timeMs - b.timeMs);
    this.index = new TimeIndex(
      this.bars,
      this.config ? TIMEFRAME_MS[this.config.timeframe] : 60_000
    );
    this.renderData();
    this.refreshMarkers();
  }

  /**
   * Updates the latest bar; older bars are ignored (out-of-order safety).
   * @param bar Bar.
   */
  updateBar(bar: ChartBar): void {
    const last = this.bars.at(-1);
    if (last && bar.timeMs < last.timeMs) return;
    const appended = !last || bar.timeMs > last.timeMs;
    if (appended) {
      this.bars.push(bar);
      this.index = new TimeIndex(
        this.bars,
        this.config ? TIMEFRAME_MS[this.config.timeframe] : 60_000
      );
    } else {
      this.bars[this.bars.length - 1] = bar;
    }
    if (!this.series || !this.config) return;
    let display = bar;
    if (this.config.chartType === 'heikin_ashi') {
      const previousRaw = this.bars.at(-2) ?? null;
      const previousHa = previousRaw
        ? (toHeikinAshi(this.bars.slice(0, -1)).at(-1) ?? null)
        : null;
      display = heikinAshiBar(bar, previousHa);
    }
    this.series.update(this.toSeriesPoint(display));
    if (appended) {
      // Studies and comparisons may already hold a value for the new bar.
      this.renderAuxData();
    } else {
      this.volume?.update(this.toVolumePoint(bar));
    }
  }

  /**
   * Converts a bar to the main series data shape.
   * @param bar Bar.
   * @return LWC data point.
   */
  private toSeriesPoint(bar: ChartBar) {
    const time = toTime(bar.timeMs);
    if (
      this.config?.chartType === 'line' ||
      this.config?.chartType === 'area'
    ) {
      return {time, value: bar.close};
    }
    return {
      time,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    };
  }

  /**
   * Converts a bar to a volume histogram point.
   * @param bar Bar.
   * @return LWC histogram point.
   */
  private toVolumePoint(bar: ChartBar) {
    return {
      time: toTime(bar.timeMs),
      value: bar.volume,
      color: withAlpha(
        bar.close >= bar.open ? this.theme.bid : this.theme.ask,
        0.32
      ),
    };
  }

  /** Pushes all bars to the series. */
  private renderData(): void {
    if (!this.series || !this.config) return;
    const display =
      this.config.chartType === 'heikin_ashi'
        ? toHeikinAshi(this.bars)
        : this.bars;
    this.series.setData(display.map(bar => this.toSeriesPoint(bar)));
    this.renderAuxData();
  }

  /**
   * Adds or replaces an overlay layer.
   * @param layer Layer.
   */
  setOverlayLayer(layer: OverlayLayer): void {
    this.removeNativeLines(layer.layerId);
    this.layers.set(layer.layerId, layer);
    this.applyNativeLayer(layer);
    this.refreshMarkers();
    this.invalidate();
  }

  /**
   * Removes an overlay layer.
   * @param layerId Layer id.
   */
  removeOverlayLayer(layerId: string): void {
    this.removeNativeLines(layerId);
    this.layers.delete(layerId);
    this.refreshMarkers();
    this.invalidate();
  }

  /**
   * Removes native price lines belonging to a layer.
   * @param layerId Layer id.
   */
  private removeNativeLines(layerId: string): void {
    for (const [key, line] of [...this.nativeLines]) {
      if (key.startsWith(`${layerId}\u0000`)) {
        this.series?.removePriceLine(line);
        this.nativeLines.delete(key);
      }
    }
  }

  /**
   * Creates native price lines (full-width lines with axis labels).
   * @param layer Layer.
   */
  private applyNativeLayer(layer: OverlayLayer): void {
    if (!this.series || !layer.visible) return;
    for (const model of layer.models) {
      if (model.kind !== 'price-line' || model.fromMs !== undefined) continue;
      const line = this.series.createPriceLine(this.priceLineOptions(model));
      this.nativeLines.set(`${layer.layerId}\u0000${model.id}`, line);
    }
  }

  /**
   * Builds native price line options.
   * @param model Price line model.
   * @return LWC options.
   */
  private priceLineOptions(model: PriceLineOverlay) {
    return {
      price: Number(model.price),
      color: model.color ?? toneColor(this.theme, model.tone),
      lineWidth: 1 as const,
      lineStyle: model.pending
        ? LineStyle.Dashed
        : lineStyleOf(model.lineStyle),
      axisLabelVisible: true,
      title: model.pending ? `${model.label} · pending` : model.label,
    };
  }

  /** Recomputes markers from all visible layers. */
  private refreshMarkers(): void {
    if (!this.markers) return;
    const snap = (timeMs: number) => this.index.snap(timeMs);
    const markers = [...this.layers.values()]
      .filter(layer => layer.visible)
      .flatMap(layer => layer.models)
      .filter((model): model is MarkerOverlay => model.kind === 'marker')
      .map(model => toLwcMarker(model, this.theme, snap))
      .sort((a, b) => Number(a.time) - Number(b.time));
    this.markers.setMarkers(markers);
  }

  /**
   * Adds a canvas layer.
   * @param layer Layer.
   * @return Disposer.
   */
  addCanvasLayer(layer: CanvasLayer): Disposer {
    this.canvasLayers.set(layer.id, layer);
    this.invalidate();
    return () => {
      this.canvasLayers.delete(layer.id);
      this.invalidate();
    };
  }

  /** Requests a repaint. */
  invalidate(): void {
    this.primitive?.update();
  }

  /**
   * Paints overlay boxes and canvas layers.
   * @param ctx Context in CSS pixels.
   */
  private paint(ctx: CanvasRenderingContext2D): void {
    const transform = this.transform();
    if (!transform) return;
    paintOverlays(ctx, [...this.layers.values()], transform, this.theme);
    const layers = [...this.canvasLayers.values()].sort(
      (a, b) => a.zIndex - b.zIndex
    );
    for (const layer of layers) {
      ctx.save();
      try {
        layer.draw(ctx, transform);
      } catch {
        // A faulty tool must not break chart rendering.
      }
      ctx.restore();
    }
  }

  /**
   * Returns current transforms.
   * @return Transform or null before mount.
   */
  transform(): CoordinateTransform | null {
    const chart = this.chart;
    const series = this.series;
    const container = this.container;
    if (!chart || !series || !container) return null;
    const timeScale = chart.timeScale();
    const index = this.index;
    const width = Math.max(0, timeScale.width());
    const paneHeight = chart.paneSize(0).height;
    const height = Math.max(
      0,
      paneHeight > 0 ? paneHeight : container.clientHeight - timeScale.height()
    );
    return {
      width,
      height,
      timeToX: timeMs => {
        const logical = index.toLogical(timeMs);
        if (logical === null) return null;
        return timeScale.logicalToCoordinate(logical as never);
      },
      xToTime: x => {
        const logical = timeScale.coordinateToLogical(x);
        return logical === null ? null : index.toTime(logical);
      },
      priceToY: price => series.priceToCoordinate(price),
      // Below the price pane (volume and study panes) there is no price.
      yToPrice: y => (y < 0 || y > height ? null : series.coordinateToPrice(y)),
    };
  }

  /**
   * Subscribes to an event.
   * @param event Event name.
   * @param handler Handler.
   * @return Disposer.
   */
  on<E extends ChartEventName>(
    event: E,
    handler: (payload: ChartEventMap[E]) => void
  ): Disposer {
    let set = this.handlers.get(event);
    if (!set) {
      set = new Set();
      this.handlers.set(event, set);
    }
    const typed = handler as (payload: never) => void;
    set.add(typed);
    return () => {
      set?.delete(typed);
    };
  }

  /**
   * Emits an event.
   * @param event Event name.
   * @param payload Payload.
   */
  private emit<E extends ChartEventName>(
    event: E,
    payload: ChartEventMap[E]
  ): void {
    for (const handler of [...(this.handlers.get(event) ?? [])]) {
      try {
        (handler as (value: ChartEventMap[E]) => void)(payload);
      } catch {
        // Listener errors are isolated.
      }
    }
  }

  /**
   * Handles LWC crosshair moves; external sync moves are not re-emitted.
   * @param param LWC mouse params.
   */
  private handleCrosshair(param: MouseEventParams<Time>): void {
    if (this.suppressCrosshair) return;
    if (!param.point || param.time === undefined) {
      if (!this.lastCrosshairNull) {
        this.lastCrosshairNull = true;
        this.emit('crosshairMove', null);
      }
      return;
    }
    if (param.sourceEvent === undefined) return;
    this.lastCrosshairNull = false;
    const inPricePane = param.paneIndex === undefined || param.paneIndex === 0;
    const price = inPricePane
      ? (this.series?.coordinateToPrice(param.point.y) ?? null)
      : null;
    this.emit('crosshairMove', {
      x: param.point.x,
      y: param.point.y,
      timeMs: Number(param.time) * 1000,
      price,
    });
  }

  /**
   * Builds a pointer event from a DOM event.
   * @param event DOM pointer event.
   * @param button Button override.
   * @return Chart pointer event.
   */
  private pointerEvent(
    event: PointerEvent,
    button?: number
  ): ChartPointerEvent {
    const rect = this.container?.getBoundingClientRect();
    const x = event.clientX - (rect?.left ?? 0) - this.leftScaleWidth();
    const y = event.clientY - (rect?.top ?? 0);
    const transform = this.transform();
    return {
      x,
      y,
      timeMs: transform?.xToTime(x) ?? null,
      price: transform?.yToPrice(y) ?? null,
      button: button ?? event.button,
      shiftKey: event.shiftKey,
      modKey: event.ctrlKey || event.metaKey,
      preventDefault: () => {
        event.preventDefault();
        if (this.interactive) this.applyInteractive(false);
      },
    };
  }

  /**
   * Width of the left (comparison) price axis, which offsets the plot.
   * @return Width in CSS pixels (0 when hidden).
   */
  private leftScaleWidth(): number {
    if (!this.chart || this.compareSpecs.length === 0) return 0;
    try {
      return this.chart.priceScale('left').width();
    } catch {
      return 0;
    }
  }

  /**
   * Routes pointer events, handling price-line drags and click synthesis.
   * @param kind Event kind.
   * @param raw DOM event.
   */
  private handlePointer(
    kind: 'pointerDown' | 'pointerMove' | 'pointerUp',
    raw: Event
  ): void {
    const event = raw as PointerEvent;
    const payload = this.pointerEvent(event);
    if (kind === 'pointerDown') {
      this.press = {x: payload.x, y: payload.y, moved: false};
      if (event.button === 0 && this.startLineDrag(payload.y)) {
        event.preventDefault();
        return;
      }
    }
    if (kind === 'pointerMove') {
      if (this.press) {
        const dx = Math.abs(payload.x - this.press.x);
        const dy = Math.abs(payload.y - this.press.y);
        if (dx > CLICK_SLOP_PX || dy > CLICK_SLOP_PX) this.press.moved = true;
      }
      if (this.drag && payload.price !== null) {
        this.drag.price = payload.price;
        this.drag.line.applyOptions({price: payload.price});
        return;
      }
    }
    if (kind === 'pointerUp') {
      const drag = this.drag;
      if (drag) {
        this.drag = null;
        this.applyInteractive(this.interactive);
        this.emit('overlayDragEnd', {
          layerId: drag.layerId,
          modelId: drag.modelId,
          price: drag.price,
        });
        this.press = null;
        return;
      }
      const clicked = this.press && !this.press.moved && event.button === 0;
      this.press = null;
      this.emit('pointerUp', payload);
      if (!this.interactive || this.isPanLocked())
        this.applyInteractive(this.interactive);
      if (clicked) this.emit('click', payload);
      return;
    }
    this.emit(kind, payload);
  }

  /**
   * Whether chart interaction is currently locked by a tool gesture.
   * @return True when locked.
   */
  private isPanLocked(): boolean {
    return this.chart?.options().handleScroll === false;
  }

  /**
   * Starts dragging a draggable price line near `y`, if any.
   * @param y Pointer y.
   * @return True when a drag started.
   */
  private startLineDrag(y: number): boolean {
    if (!this.series) return false;
    for (const layer of this.layers.values()) {
      if (!layer.visible) continue;
      for (const model of layer.models) {
        if (model.kind !== 'price-line' || !model.draggable) continue;
        const line = this.nativeLines.get(`${layer.layerId}\u0000${model.id}`);
        const lineY = this.series.priceToCoordinate(Number(model.price));
        if (
          line &&
          lineY !== null &&
          Math.abs(lineY - y) <= DRAG_TOLERANCE_PX
        ) {
          this.drag = {
            layerId: layer.layerId,
            modelId: model.id,
            line,
            price: Number(model.price),
          };
          this.applyInteractive(false);
          return true;
        }
      }
    }
    return false;
  }

  /**
   * Shows a synchronized crosshair without re-emitting it.
   * @param point Point or null.
   */
  setCrosshair(point: {timeMs: number; price: number} | null): void {
    if (!this.chart || !this.series) return;
    this.suppressCrosshair = true;
    try {
      if (point === null) {
        this.chart.clearCrosshairPosition();
      } else {
        const snapped = this.index.snap(point.timeMs);
        if (snapped !== null) {
          this.chart.setCrosshairPosition(
            point.price,
            toTime(snapped),
            this.series
          );
        }
      }
    } finally {
      this.suppressCrosshair = false;
    }
  }

  /**
   * Converts a logical range to a time range.
   * @param range Logical range.
   * @return Time range or null.
   */
  private logicalToRange(range: LogicalRange): TimeRange | null {
    const from = this.index.toTime(range.from);
    const to = this.index.toTime(range.to);
    return from === null || to === null ? null : {fromMs: from, toMs: to};
  }

  /**
   * Sets the visible range.
   * @param range Range.
   */
  setVisibleRange(range: TimeRange): void {
    const from = this.index.toLogical(range.fromMs);
    const to = this.index.toLogical(range.toMs);
    if (from === null || to === null) return;
    this.chart?.timeScale().setVisibleLogicalRange({from, to});
  }

  /** Fits all bars. */
  fitContent(): void {
    this.chart?.timeScale().fitContent();
  }

  /**
   * Enables or disables pan/zoom.
   * @param enabled Whether interaction is enabled.
   */
  setInteractive(enabled: boolean): void {
    this.interactive = enabled;
    this.applyInteractive(enabled);
  }

  /**
   * Applies interaction options.
   * @param enabled Whether enabled.
   */
  private applyInteractive(enabled: boolean): void {
    this.chart?.applyOptions({handleScroll: enabled, handleScale: enabled});
  }

  /**
   * Serializes view state.
   * @return State.
   */
  serialize(): ChartViewState {
    const range = this.chart?.timeScale().getVisibleLogicalRange();
    return {visibleRange: range ? this.logicalToRange(range) : null};
  }

  /**
   * Restores view state.
   * @param state State.
   */
  restore(state: ChartViewState): void {
    if (state.visibleRange) this.setVisibleRange(state.visibleRange);
  }

  /**
   * Takes a PNG screenshot.
   * @return Data URL or null.
   */
  screenshot(): string | null {
    try {
      return this.chart?.takeScreenshot().toDataURL('image/png') ?? null;
    } catch {
      return null;
    }
  }

  /** Releases all resources. */
  destroy(): void {
    for (const cleanup of this.cleanups.splice(0).reverse()) {
      try {
        cleanup();
      } catch {
        // Continue releasing the rest.
      }
    }
    this.handlers.clear();
    this.layers.clear();
    this.nativeLines.clear();
    this.canvasLayers.clear();
    this.markers = null;
    this.primitive = null;
    this.volumePrimitive = null;
    this.studies.clear();
    this.compareSeries.clear();
    this.compareSpecs = [];
    this.chart?.remove();
    this.chart = null;
    this.series = null;
    this.volume = null;
    this.container = null;
  }

  /**
   * Number of active listeners (leak diagnostics).
   * @return Count of port listeners plus tracked DOM/LWC cleanups.
   */
  listenerCount(): number {
    return (
      [...this.handlers.values()].reduce((sum, set) => sum + set.size, 0) +
      this.cleanups.length
    );
  }
}

/**
 * Creates a Lightweight Charts port.
 * @return New chart port.
 */
export function createLwcChartPort(): LwcChartPort {
  return new LwcChartPort();
}
