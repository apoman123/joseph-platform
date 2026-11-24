/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview `DrawingSeriesSync`: keeps the engine series of every
 * engine-backed drawing on one chart up to date.
 *
 * It watches the drawing store, asks each tool for the request its anchors
 * imply (`ToolPlugin.seriesRequest`) and fetches it through the injected
 * `getIndicatorSeries` client. A request is re-sent when it changes (the
 * anchor moved, an input changed; debounced so a drag sends one request)
 * and on `refresh()`, which the host calls when a bar closes. Responses
 * for superseded requests are dropped. A failed refresh keeps the last
 * good values (they stay correct as of their `as_of_ms`); a failed first
 * request is reported as an error. When the engine refuses a request (a
 * 4xx answer, e.g. an anchor beyond the bars it computes from) its own
 * message is kept so the drawing shows exactly what the engine said.
 */

import {
  ApiError,
  type BarCalendar,
  type IndicatorSeries,
  type IndicatorSeriesQuery,
  type InstrumentId,
  type Timeframe,
} from '@joseph/contracts';
import {appliesToTimeframe} from './document';
import {
  seriesRequestKey,
  type DrawingSeriesRequest,
  type DrawingSeriesState,
} from './series';
import type {DrawingStore} from './store';
import type {ToolRegistry} from './tool';

/** Environment of the sync. */
export interface DrawingSeriesSyncOptions {
  /** Market client call (`MarketDataClient.getIndicatorSeries`). */
  fetch: (query: IndicatorSeriesQuery) => Promise<IndicatorSeries>;
  /** Instrument shown on the chart. */
  instrumentId: () => InstrumentId;
  /** Timeframe shown on the chart. */
  timeframe: () => Timeframe;
  /**
   * Session calendar of the instrument (anchor bars on session
   * boundaries); UTC bars when absent.
   */
  calendar?: () => BarCalendar;
  /** Called after any state change (repaint, re-render lists). */
  onChange: () => void;
  /** Delay in milliseconds before a changed request is sent (default 200). */
  debounceMs?: number;
}

/** Tracked request of one drawing. */
interface Entry {
  /** Request identity (`seriesRequestKey`). */
  key: string;
  /** Latest state. */
  state: DrawingSeriesState;
  /** Increments per fetch; responses of older fetches are dropped. */
  seq: number;
  /** Pending (debounced) fetch, or null. */
  timer: ReturnType<typeof setTimeout> | null;
}

/** Engine series of the engine-backed drawings on one chart. */
export class DrawingSeriesSync {
  private readonly entries = new Map<string, Entry>();
  private readonly disposers: Array<() => void> = [];
  private disposed = false;

  /**
   * Creates the sync and requests series for existing drawings.
   * @param store Drawing store of the chart.
   * @param tools Tool registry.
   * @param options Fetch client, chart accessors and change callback.
   */
  constructor(
    private readonly store: DrawingStore,
    private readonly tools: ToolRegistry,
    private readonly options: DrawingSeriesSyncOptions
  ) {
    this.disposers.push(
      store.subscribe(() => void this.reconcile()),
      tools.subscribe(() => void this.reconcile())
    );
    this.reconcile();
  }

  /**
   * Returns the series state of a drawing.
   * @param drawingId Drawing id.
   * @return State, or null when the drawing has no series request (or is
   *     hidden, or on another instrument or timeframe).
   */
  get(drawingId: string): DrawingSeriesState | null {
    return this.entries.get(drawingId)?.state ?? null;
  }

  /** Re-requests every series now (call when a bar closes). */
  refresh(): void {
    for (const [id, entry] of this.entries) {
      this.schedule(id, entry, 0);
    }
  }

  /**
   * Aligns tracked requests with the store: drops entries of drawings that
   * no longer need a series and schedules a debounced fetch for new or
   * changed requests. Called on every store or registry change.
   */
  reconcile(): void {
    if (this.disposed) return;
    const timeframe = this.options.timeframe();
    const instrumentId = this.options.instrumentId();
    const wanted = new Map<
      string,
      {request: DrawingSeriesRequest; key: string}
    >();
    for (const doc of this.store.get().drawings) {
      if (
        !doc.visible ||
        doc.instrument_id !== instrumentId ||
        !appliesToTimeframe(doc, timeframe)
      ) {
        continue;
      }
      const request =
        this.tools.get(doc.tool_id)?.seriesRequest?.(doc, {
          timeframe,
          calendar: this.options.calendar?.(),
        }) ?? null;
      if (request) {
        wanted.set(doc.drawing_id, {
          request,
          key: seriesRequestKey(request, timeframe),
        });
      }
    }
    let changed = false;
    for (const [id, entry] of this.entries) {
      if (wanted.has(id)) continue;
      if (entry.timer) clearTimeout(entry.timer);
      this.entries.delete(id);
      changed = true;
    }
    for (const [id, {request, key}] of wanted) {
      const current = this.entries.get(id);
      // An unchanged request keeps its state and pending fetch, so store
      // changes that do not move the anchors cost nothing.
      if (current?.key === key) continue;
      if (current?.timer) clearTimeout(current.timer);
      const entry: Entry = {
        key,
        seq: 0,
        timer: null,
        state: {
          status: 'loading',
          request,
          timeframe,
          calendar: this.options.calendar?.(),
          points: [],
          asOfMs: null,
          error: null,
          refusal: null,
        },
      };
      this.entries.set(id, entry);
      this.schedule(id, entry, this.options.debounceMs ?? 200);
      changed = true;
    }
    if (changed) this.options.onChange();
  }

  /**
   * Schedules a fetch for an entry, replacing any pending one (debounce).
   * @param id Drawing id.
   * @param entry Entry to fetch.
   * @param delayMs Delay in milliseconds.
   */
  private schedule(id: string, entry: Entry, delayMs: number): void {
    if (entry.timer) clearTimeout(entry.timer);
    entry.timer = setTimeout(() => {
      entry.timer = null;
      this.load(id, entry);
    }, delayMs);
  }

  /**
   * Fetches an entry's series and applies the response if still current.
   * @param id Drawing id.
   * @param entry Entry to fetch.
   */
  private load(id: string, entry: Entry): void {
    entry.seq += 1;
    const seq = entry.seq;
    const {request, timeframe} = entry.state;
    // A response applies only while the sync is alive, the drawing still
    // has this entry (a changed request replaces it) and no later fetch of
    // the entry was started; out-of-order responses are dropped.
    const current = () =>
      !this.disposed && this.entries.get(id) === entry && entry.seq === seq;
    this.options
      .fetch({
        instrumentId: this.options.instrumentId(),
        timeframe,
        pluginId: request.pluginId,
        fromMs: request.fromMs,
        settings: request.settings,
      })
      .then(series => {
        if (!current()) return;
        entry.state = {
          ...entry.state,
          status: 'ready',
          points: [...series.points].sort(
            (a, b) => a.bar_start_ms - b.bar_start_ms
          ),
          asOfMs: series.as_of_ms,
          error: null,
          refusal: null,
        };
        this.options.onChange();
      })
      .catch((failure: unknown) => {
        // A failed refresh keeps the last good values: they stay correct as
        // of their `as_of_ms`.
        if (!current() || entry.state.status === 'ready') return;
        const refused =
          failure instanceof ApiError &&
          failure.status >= 400 &&
          failure.status < 500 &&
          failure.body.message
            ? failure.body.message
            : null;
        entry.state = {
          ...entry.state,
          status: 'error',
          error: failure instanceof Error ? failure.message : 'request failed',
          refusal: refused,
        };
        this.options.onChange();
      });
  }

  /** Stops watching and drops pending requests. */
  dispose(): void {
    this.disposed = true;
    for (const dispose of this.disposers.splice(0)) {
      dispose();
    }
    for (const entry of this.entries.values()) {
      if (entry.timer) clearTimeout(entry.timer);
    }
    this.entries.clear();
  }
}
