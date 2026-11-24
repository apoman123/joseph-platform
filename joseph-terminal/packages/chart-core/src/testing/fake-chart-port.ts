/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview In-memory `ChartPort` used by unit and component tests.
 *
 * Uses a linear transform: each bar is `barWidth` pixels wide starting at
 * `originMs`, and prices map linearly between `priceTop` and `priceBottom`.
 * Tests drive pointer events with `emit`.
 */

import type {
  CanvasLayer,
  ChartBar,
  ChartEventMap,
  ChartEventName,
  ChartPort,
  ChartViewState,
  CompareSeriesSpec,
  CoordinateTransform,
  Disposer,
  SeriesConfig,
  StudyPaneSpec,
  TimeRange,
} from '../chart-port';
import type {OverlayLayer} from '../overlay-model';

/** Geometry options of the fake transform. */
export interface FakeChartGeometry {
  /** Time at x = 0 (UTC ms). */
  originMs: number;
  /** Bar duration (ms). */
  durationMs: number;
  /** Pixels per bar. */
  barWidth: number;
  /** Price at y = 0. */
  priceTop: number;
  /** Price at y = height. */
  priceBottom: number;
  /** Plot width (CSS pixels). */
  width: number;
  /** Plot height (CSS pixels). */
  height: number;
}

/** Recording fake chart; tests read its public fields. */
export class FakeChartPort implements ChartPort {
  /** Whether `mount` has been called. */
  mounted = false;
  /** Whether `destroy` has been called. */
  destroyed = false;
  /** Last value passed to `setInteractive`. */
  interactive = true;
  /** Last series configuration. */
  config: SeriesConfig | null = null;
  /** Bars, sorted by time. */
  bars: ChartBar[] = [];
  /** Overlay layers by id. */
  readonly layers = new Map<string, OverlayLayer>();
  /** Canvas layers by id (never drawn by the fake). */
  readonly canvasLayers = new Map<string, CanvasLayer>();
  /** Study panes by id. */
  readonly studyPanes = new Map<string, StudyPaneSpec>();
  /** Last comparison series. */
  compare: readonly CompareSeriesSpec[] = [];
  /** Last synchronized crosshair. */
  crosshair: {timeMs: number; price: number} | null = null;
  /** Last visible range set (null after `fitContent`). */
  visibleRange: TimeRange | null = null;
  /** Number of `invalidate` calls. */
  invalidations = 0;
  private readonly handlers = new Map<string, Set<(payload: never) => void>>();

  /**
   * Creates the fake.
   * @param geometry Transform geometry.
   */
  constructor(
    readonly geometry: FakeChartGeometry = {
      originMs: 0,
      durationMs: 60_000,
      barWidth: 10,
      priceTop: 200,
      priceBottom: 100,
      width: 1000,
      height: 500,
    }
  ) {}

  /** Marks the fake as mounted; it needs no container or theme. */
  mount(): void {
    this.mounted = true;
  }

  /**
   * Records series config; switching instruments clears bars and layers.
   * @param config Series configuration.
   */
  setSeries(config: SeriesConfig): void {
    if (
      this.config &&
      (this.config.instrumentId !== config.instrumentId ||
        this.config.timeframe !== config.timeframe)
    ) {
      this.bars = [];
      this.layers.clear();
      this.studyPanes.clear();
      this.compare = [];
    }
    this.config = config;
  }

  /**
   * Replaces bars.
   * @param bars Bars.
   */
  setBars(bars: readonly ChartBar[]): void {
    const byTime = new Map(bars.map(bar => [bar.timeMs, bar]));
    this.bars = [...byTime.values()].sort((a, b) => a.timeMs - b.timeMs);
  }

  /**
   * Applies the latest bar; older bars are ignored.
   * @param bar Bar.
   */
  updateBar(bar: ChartBar): void {
    const last = this.bars.at(-1);
    if (last && bar.timeMs < last.timeMs) return;
    if (last && bar.timeMs === last.timeMs) {
      this.bars[this.bars.length - 1] = bar;
    } else {
      this.bars.push(bar);
    }
  }

  /**
   * Stores a layer.
   * @param layer Layer.
   */
  setOverlayLayer(layer: OverlayLayer): void {
    this.layers.set(layer.layerId, layer);
  }

  /**
   * Removes a layer.
   * @param layerId Layer id.
   */
  removeOverlayLayer(layerId: string): void {
    this.layers.delete(layerId);
  }

  /**
   * Stores a canvas layer.
   * @param layer Layer.
   * @return Disposer.
   */
  addCanvasLayer(layer: CanvasLayer): Disposer {
    this.canvasLayers.set(layer.id, layer);
    return () => {
      this.canvasLayers.delete(layer.id);
    };
  }

  /** Counts repaint requests. */
  invalidate(): void {
    this.invalidations += 1;
  }

  /**
   * Returns the linear transform (available even before mount).
   * @return Transform.
   */
  transform(): CoordinateTransform {
    const g = this.geometry;
    const span = g.priceTop - g.priceBottom;
    return {
      width: g.width,
      height: g.height,
      timeToX: t => ((t - g.originMs) / g.durationMs) * g.barWidth,
      xToTime: x => g.originMs + (x / g.barWidth) * g.durationMs,
      priceToY: p => ((g.priceTop - p) / span) * g.height,
      yToPrice: y => g.priceTop - (y / g.height) * span,
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
   * Emits an event to subscribers (test driver). Unlike the LWC adapter it
   * does not catch handler errors, so tests see them.
   * @param event Event name.
   * @param payload Payload.
   */
  emit<E extends ChartEventName>(event: E, payload: ChartEventMap[E]): void {
    // Handlers in the set for `event` were registered with its payload type.
    for (const handler of [...(this.handlers.get(event) ?? [])]) {
      (handler as (value: ChartEventMap[E]) => void)(payload);
    }
  }

  /**
   * Number of active listeners (leak checks).
   * @return Listener count.
   */
  listenerCount(): number {
    return [...this.handlers.values()].reduce((sum, set) => sum + set.size, 0);
  }

  /**
   * Records the synced crosshair.
   * @param point Point or null.
   */
  setCrosshair(point: {timeMs: number; price: number} | null): void {
    this.crosshair = point;
  }

  /**
   * Records the visible range.
   * @param range Range.
   */
  setVisibleRange(range: TimeRange): void {
    this.visibleRange = range;
  }

  /** Clears the recorded visible range, as fitting shows every bar. */
  fitContent(): void {
    this.visibleRange = null;
  }

  /**
   * Stores a study pane.
   * @param spec Study spec.
   */
  setStudyPane(spec: StudyPaneSpec): void {
    this.studyPanes.set(spec.id, spec);
  }

  /**
   * Removes a study pane.
   * @param id Study id.
   */
  removeStudyPane(id: string): void {
    this.studyPanes.delete(id);
  }

  /**
   * Stores comparison series.
   * @param series Comparison specs.
   */
  setCompareSeries(series: readonly CompareSeriesSpec[]): void {
    this.compare = series;
  }

  /**
   * Records interactivity.
   * @param enabled Whether pan/zoom is enabled.
   */
  setInteractive(enabled: boolean): void {
    this.interactive = enabled;
  }

  /**
   * Serializes view state.
   * @return State.
   */
  serialize(): ChartViewState {
    return {visibleRange: this.visibleRange};
  }

  /**
   * Restores view state.
   * @param state State.
   */
  restore(state: ChartViewState): void {
    this.visibleRange = state.visibleRange;
  }

  /**
   * Screenshots are unsupported in the fake.
   * @return Always null.
   */
  screenshot(): string | null {
    return null;
  }

  /** Marks the fake destroyed and drops its listeners, layers and panes. */
  destroy(): void {
    this.destroyed = true;
    this.handlers.clear();
    this.layers.clear();
    this.canvasLayers.clear();
    this.studyPanes.clear();
    this.compare = [];
  }

  /**
   * Builds a pointer event at pixel coordinates (test helper); time and
   * price come from the linear transform.
   * @param x X in CSS pixels.
   * @param y Y in CSS pixels.
   * @param extra Fields to override.
   * @return Pointer event whose `prevented` flag records `preventDefault`.
   */
  pointer(
    x: number,
    y: number,
    extra: Partial<ChartEventMap['pointerDown']> = {}
  ): ChartEventMap['pointerDown'] & {prevented: boolean} {
    const t = this.transform();
    const event = {
      x,
      y,
      timeMs: t.xToTime(x),
      price: t.yToPrice(y),
      button: 0,
      shiftKey: false,
      modKey: false,
      prevented: false,
      preventDefault() {
        event.prevented = true;
      },
      ...extra,
    };
    return event;
  }
}

/** Recorded 2D context call. */
export interface CanvasCall {
  /** Method or property name. */
  op: string;
  /** Arguments or assigned value. */
  args: unknown[];
}

/**
 * Creates a recording fake 2D context (jsdom has no canvas).
 * @return Context and its call log.
 */
export function createRecordingContext(): {
  ctx: CanvasRenderingContext2D;
  calls: CanvasCall[];
} {
  const calls: CanvasCall[] = [];
  const target: Record<string, unknown> = {
    measureText: (text: string) => ({width: text.length * 6}),
  };
  const ctx = new Proxy(target, {
    get(obj, prop: string) {
      if (prop in obj) return obj[prop];
      return (...args: unknown[]) => {
        calls.push({op: prop, args});
      };
    },
    set(obj, prop: string, value: unknown) {
      calls.push({op: `set:${prop}`, args: [value]});
      // Stored under a prefixed key, so reading the property back still
      // yields a recording function rather than the assigned value.
      obj[`__${prop}`] = value;
      return true;
    },
  });
  // A deliberate partial fake: any method call is recorded, which covers
  // what the painters use.
  return {ctx: ctx as unknown as CanvasRenderingContext2D, calls};
}
