/**
 * @fileoverview DEMO implementation of `MarketDataClient` and a simulated
 * market stream. Every record carries `feed_state: 'DEMO'`.
 */
import {
  Decimal,
  TIMEFRAME_MS,
  TIMEFRAMES,
  floorToTimeframe,
  type AoiList,
  type AoiRecord,
  type CandleBatch,
  type CandleQuery,
  type ChannelSpec,
  type IndicatorPluginInfo,
  type IndicatorPluginList,
  type IndicatorSnapshotList,
  type Instrument,
  type InstrumentList,
  type InstrumentQuery,
  type MarketDataClient,
  type MarketSnapshot,
  type OrderBook,
  type QualificationRecord,
  type QualificationReport,
  type QualificationStatus,
  type SeriesQuery,
  type StreamClient,
  type StreamEnvelope,
  type StreamHandler,
  type StreamStatus,
  type StructureEvent,
  type Venue,
  type VenueStatusList,
} from '@joseph/contracts';
import {DEMO_AOIS, DEMO_EVENTS} from './demo-indicators';
import {
  DEMO_INSTRUMENT_SPECS,
  demoCandle,
  demoSeries,
  priceAt,
  type DemoInstrumentSpec,
} from './prices';

/** Default history length. */
const DEFAULT_LIMIT = 400;

/**
 * Looks up a DEMO instrument.
 * @param instrumentId Canonical id.
 * @return Spec.
 * @throws {Error} For non-DEMO instruments (DEMO never fakes real venues).
 */
export function demoSpec(instrumentId: string): DemoInstrumentSpec {
  const spec = DEMO_INSTRUMENT_SPECS.find(s => s.instrumentId === instrumentId);
  if (!spec) {
    throw new Error(
      `Demo data exists only for the demo venue; ${instrumentId} needs a connected market engine`
    );
  }
  return spec;
}

/**
 * Builds the contract instrument for a DEMO spec.
 * @param spec Spec.
 * @param nowMs Clock.
 * @return Instrument.
 */
export function demoInstrument(
  spec: DemoInstrumentSpec,
  nowMs: number
): Instrument {
  return {
    instrument_id: spec.instrumentId,
    key: {
      venue: 'mock',
      market_type: 'perp',
      venue_market_id: spec.displaySymbol,
      canonical_base: spec.base,
      quote: 'USD',
      settlement: 'USD',
      instrument_version: 1,
    },
    display_symbol: spec.displaySymbol,
    tick_size: spec.tickSize,
    lot_size: spec.lotSize,
    min_qty: spec.lotSize,
    max_qty: null,
    min_notional: '5',
    contract_multiplier: '1',
    linear: true,
    status: 'TRADING',
    funding_interval_hours: 8,
    max_leverage: '20',
    asset_class: 'CRYPTO',
    source: 'mock',
    as_of_ms: nowMs,
  };
}

/** Static descriptors of the backend indicator plugins. */
const PLUGINS: readonly [string, string, string][] = [
  [
    'daily_bias',
    'Daily bias',
    'Confirmed BOS/CHoCH per timeframe, sessions and prior-day levels.',
  ],
  [
    'fvg_ifvg',
    'FVG and IFVG',
    'Three-candle fair value gaps and their inversions.',
  ],
  [
    'order_breaker_blocks',
    'Order and breaker blocks',
    'Order blocks behind structure breaks and their breaker conversions.',
  ],
  [
    'liquidity_sweeps',
    'Liquidity sweeps',
    'Buy-side and sell-side liquidity pools, equal highs/lows and sweeps.',
  ],
  [
    'order_flow_pressure',
    'Order flow pressure',
    'Estimated lower-timeframe volume delta and failure/breakout events.',
  ],
  [
    'auction_value_areas',
    'Auction value areas',
    'Rolling VAH/POC/VAL from 1H and chart-timeframe bars.',
  ],
  [
    'premium_discount_ote',
    'Premium, discount and OTE',
    'Dealing range, equilibrium and 62-79% optimal trade entry.',
  ],
];

/** Options for the DEMO client. */
export interface DemoMarketOptions {
  /** Clock (injectable for tests). */
  now?: () => number;
}

/** DEMO market-data client. */
export class DemoMarketDataClient implements MarketDataClient {
  private readonly now: () => number;

  /**
   * Creates the client.
   * @param options Options.
   */
  constructor(options: DemoMarketOptions = {}) {
    this.now = options.now ?? (() => Date.now());
  }

  /**
   * Lists the DEMO venue.
   * @return Venue status.
   */
  async listVenues(): Promise<VenueStatusList> {
    const now = this.now();
    return {
      as_of_ms: now,
      venues: [
        {
          capabilities: {
            venue: 'mock',
            display_name: 'Demo venue',
            market_types: ['perp'],
            market_data: {
              rest_history: true,
              native_ws_candles: true,
              native_ws_trades: false,
              native_ws_book: false,
              book_snapshot: true,
              open_interest: true,
              funding: true,
              trades_history: false,
            },
            timeframes: [...TIMEFRAMES],
            notes: [
              'Synthetic data generated in the browser. Not a real market.',
            ],
            docs_url: '',
            verified_at_ms: null,
          },
          health: {
            venue: 'mock',
            state: 'HEALTHY',
            feed_mode: 'AUTO',
            feed_state: 'DEMO',
            transport: 'MOCK',
            last_message_ms: now,
            lag_ms: 0,
            reconnects: 0,
            gaps_detected: 0,
            last_error: null,
            as_of_ms: now,
          },
        },
      ],
    };
  }

  /**
   * Searches DEMO instruments.
   * @param query Filters.
   * @return Instruments.
   */
  async listInstruments(query: InstrumentQuery): Promise<InstrumentList> {
    const now = this.now();
    const q = (query.q ?? '').trim().toUpperCase();
    const venueOk = !query.venue || query.venue === 'mock';
    const instruments = venueOk
      ? DEMO_INSTRUMENT_SPECS.filter(
          spec => !q || spec.displaySymbol.includes(q) || spec.base.includes(q)
        ).map(spec => demoInstrument(spec, now))
      : [];
    return {instruments, as_of_ms: now};
  }

  /**
   * Builds DEMO candles.
   * @param query Series and range.
   * @return Candle batch.
   */
  async getCandles(query: CandleQuery): Promise<CandleBatch> {
    const spec = demoSpec(query.instrumentId);
    const now = this.now();
    const duration = TIMEFRAME_MS[query.timeframe];
    const toMs = query.toMs ?? now;
    const limit =
      query.fromMs !== undefined
        ? Math.min(
            2000,
            Math.max(1, Math.ceil((toMs - query.fromMs) / duration))
          )
        : Math.min(2000, query.limit ?? DEFAULT_LIMIT);
    return {
      schema_version: 1,
      instrument_id: spec.instrumentId,
      timeframe: query.timeframe,
      candles: demoSeries(spec, query.timeframe, limit, now, toMs),
      feed_state: 'DEMO',
      source: 'mock',
      as_of_ms: now,
      complete: true,
      gaps: [],
    };
  }

  /**
   * Builds a DEMO snapshot.
   * @param instrumentId Instrument.
   * @return Snapshot.
   */
  async getSnapshot(instrumentId: string): Promise<MarketSnapshot> {
    const spec = demoSpec(instrumentId);
    const now = this.now();
    const tick = Decimal.parse(spec.tickSize);
    const last = Decimal.fromNumber(priceAt(spec, now), 10).roundToStep(tick);
    const dayAgo = priceAt(spec, now - 86_400_000);
    const change = ((last.toNumber() - dayAgo) / dayAgo) * 100;
    return {
      instrument_id: instrumentId,
      last: last.toString(),
      mark: last.toString(),
      index: last.toString(),
      best_bid: last.sub(tick).toString(),
      best_ask: last.add(tick).toString(),
      spread_pct: tick.add(tick).div(last).mul(Decimal.parse('100')).toFixed(6),
      turnover_24h_quote: Decimal.fromNumber(
        spec.volumePerMinute * 1440 * spec.basePrice,
        0
      ).toString(),
      volume_24h_base: Decimal.fromNumber(
        spec.volumePerMinute * 1440,
        0
      ).toString(),
      change_24h_pct: Decimal.fromNumber(change, 4).toString(),
      funding: {
        instrument_id: instrumentId,
        rate: '0.0001',
        interval_hours: 8,
        normalized_8h_pct: '0.01',
        kind: 'PREDICTED',
        effective_time_ms: floorToTimeframe('1h', now),
        next_funding_ms: floorToTimeframe('1h', now) + 3_600_000,
        source: 'mock',
        as_of_ms: now,
      },
      open_interest: {
        instrument_id: instrumentId,
        base_quantity: Decimal.fromNumber(
          spec.volumePerMinute * 900,
          0
        ).toString(),
        notional_usd: Decimal.fromNumber(
          spec.volumePerMinute * 900 * spec.basePrice,
          0
        ).toString(),
        mark_price: last.toString(),
        source: 'mock',
        as_of_ms: now,
      },
      feed_state: 'DEMO',
      source: 'mock',
      as_of_ms: now,
      data_age_ms: 0,
      stale: false,
    };
  }

  /**
   * Builds a DEMO order book.
   * @param instrumentId Instrument.
   * @param depth Levels.
   * @return Book.
   */
  async getOrderBook(instrumentId: string, depth = 20): Promise<OrderBook> {
    const snapshot = await this.getSnapshot(instrumentId);
    const spec = demoSpec(instrumentId);
    const tick = Decimal.parse(spec.tickSize);
    const bid = Decimal.parse(snapshot.best_bid ?? '0');
    const ask = Decimal.parse(snapshot.best_ask ?? '0');
    const level = (i: number) =>
      Decimal.fromNumber(spec.volumePerMinute * 0.05 * (1 + i * 0.4), 4)
        .floorToStep(Decimal.parse(spec.lotSize))
        .toString();
    const now = this.now();
    return {
      instrument_id: instrumentId,
      venue_sequence: Math.floor(now / 1000),
      snapshot_ts_ms: now,
      received_at_ms: now,
      bids: Array.from({length: depth}, (_, i) => [
        bid.sub(tick.mul(Decimal.parse(String(i)))).toString(),
        level(i),
      ]),
      asks: Array.from({length: depth}, (_, i) => [
        ask.add(tick.mul(Decimal.parse(String(i)))).toString(),
        level(i),
      ]),
      depth,
      stale: false,
      source: 'mock',
      checksum_verified: null,
    };
  }

  /**
   * Returns the DEMO qualification report.
   * @param venue Venue filter.
   * @param status Status filter.
   * @return Report.
   */
  async getQualification(
    venue?: Venue,
    status?: QualificationStatus
  ): Promise<QualificationReport> {
    const now = this.now();
    const records = demoQualification(now).filter(
      record =>
        (!venue || record.venue === venue) &&
        (!status || record.status === status)
    );
    return {
      policy_version: 'demo-1',
      as_of_ms: now,
      venue: venue ?? null,
      records,
    };
  }

  /**
   * Lists indicator plugin descriptors.
   * @return Plugins.
   */
  async listIndicatorPlugins(): Promise<IndicatorPluginList> {
    const plugins: IndicatorPluginInfo[] = PLUGINS.map(
      ([id, name, description]) => ({
        plugin_id: id,
        indicator_version: '1.0.0',
        schema_version: 1,
        display_name: name,
        description,
        timeframes: [...TIMEFRAMES],
        warmup_bars: 50,
        required_streams: ['candles:chart'],
        input_schema: {},
        output_kinds: [],
        output_events: [],
        licensing_status: 'CLEAN_ROOM_FROM_SPEC',
        parity_status: 'SPEC_ONLY',
      })
    );
    return {plugins};
  }

  /**
   * Returns DEMO snapshots (AOIs grouped by plugin).
   * @param query Series query.
   * @return Snapshots.
   */
  async getIndicatorSnapshots(
    query: SeriesQuery
  ): Promise<IndicatorSnapshotList> {
    const list = await this.getAois(query);
    const sources = new Set([
      ...list.aois.map(a => a.source),
      ...(list.events ?? []).map(e => e.source),
    ]);
    return {
      as_of_ms: list.as_of_ms,
      snapshots: [...sources].map(source => ({
        schema_version: 1,
        instrument_id: list.instrument_id,
        timeframe: list.timeframe,
        plugin_id: source,
        indicator_version: '1.0.0',
        method: 'pine_parity',
        as_of_ms: list.as_of_ms,
        bar_start_ms: list.as_of_ms - TIMEFRAME_MS[list.timeframe],
        aois: list.aois.filter(a => a.source === source),
        events: (list.events ?? []).filter(e => e.source === source),
        values: {},
        warmup_complete: true,
        data_quality: {feed_state: 'DEMO', missing_bars: 0, coverage_pct: null},
      })),
    };
  }

  /**
   * Materializes the relative DEMO AOI fixture for a series.
   * @param query Series query.
   * @return AOI list.
   */
  async getAois(query: SeriesQuery): Promise<AoiList> {
    const spec = demoSpec(query.instrumentId);
    const now = this.now();
    const duration = TIMEFRAME_MS[query.timeframe];
    const lastStart = floorToTimeframe(query.timeframe, now) - duration;
    const asOf = lastStart + duration;
    const close = Decimal.parse(
      demoCandle(spec, query.timeframe, lastStart, now).close
    );
    const tick = Decimal.parse(spec.tickSize);
    const at = (ratio: number) =>
      close.mul(Decimal.fromNumber(ratio, 6)).roundToStep(tick).toString();
    const wanted =
      query.plugins && query.plugins.length > 0 ? new Set(query.plugins) : null;
    const aois: AoiRecord[] = DEMO_AOIS.filter(
      a => !wanted || wanted.has(a.source)
    ).map(a => {
      const created = lastStart - a.barsAgo * duration;
      const ended = a.state === 'swept' || a.state === 'invalidated';
      return {
        schema_version: 1,
        type: 'aoi.updated',
        instrument_id: spec.instrumentId,
        timeframe: query.timeframe,
        aoi_id: `demo-${a.key}-${query.timeframe}`,
        source: a.source,
        kind: a.kind,
        side: a.side,
        state: a.state,
        lower: at(a.lowerRatio),
        upper: at(a.upperRatio),
        created_at_ms: created,
        confirmed_at_ms: created + (a.confirmBars + 1) * duration,
        invalidated_at_ms: ended
          ? lastStart - Math.floor(a.barsAgo / 3) * duration
          : null,
        as_of_ms: asOf,
        quality_score: a.score,
        range_id: a.rangeId ?? null,
        indicator_version: '1.0.0',
        method: 'pine_parity',
        meta: {demo: true},
      };
    });
    const events: StructureEvent[] = DEMO_EVENTS.filter(
      e => !wanted || wanted.has(e.source)
    ).map(e => {
      const start = lastStart - e.barsAgo * duration;
      return {
        event_id: `demo-${e.key}-${query.timeframe}`,
        instrument_id: spec.instrumentId,
        timeframe: query.timeframe,
        source: e.source,
        name: e.name,
        bar_start_ms: start,
        confirmed_at_ms: start + duration,
        price: at(e.priceRatio),
        aoi_id: null,
        score: null,
        meta: {demo: true},
      };
    });
    return {
      instrument_id: spec.instrumentId,
      timeframe: query.timeframe,
      aois,
      events,
      as_of_ms: asOf,
      feed_state: 'DEMO',
    };
  }
}

/**
 * Static DEMO qualification records with strict gates.
 * @param now Clock.
 * @return Records.
 */
function demoQualification(now: number): QualificationRecord[] {
  const gate = (
    gate: QualificationRecord['gates'][number]['gate'],
    observed: string | null,
    threshold: string,
    comparator: 'gt' | 'lt',
    unit: 'USD' | 'ratio' | 'pct'
  ) => {
    const passed =
      observed === null
        ? null
        : comparator === 'gt'
          ? Decimal.parse(observed).cmp(Decimal.parse(threshold)) > 0
          : Decimal.parse(observed).cmp(Decimal.parse(threshold)) < 0;
    return {
      gate,
      observed,
      threshold,
      comparator,
      passed,
      unit,
      as_of_ms: observed === null ? null : now,
      reason:
        observed === null
          ? 'No data; never treated as passing'
          : passed
            ? 'Passes'
            : `Needs ${comparator === 'gt' ? 'more than' : 'less than'} ${threshold}`,
    };
  };
  const base = {
    venue: 'mock' as const,
    asset_class: 'CRYPTO' as const,
    policy_version: 'demo-1',
    as_of_ms: now,
  };
  return [
    {
      ...base,
      instrument_id: 'mock:perp:BTCUSD',
      display_symbol: 'BTCUSD',
      status: 'QUALIFIED',
      risk_tags: [],
      gates: [
        gate('TURNOVER_24H', '5745600000', '50000000', 'gt', 'USD'),
        gate('OPEN_INTEREST_USD', '3591000000', '10000000', 'gt', 'USD'),
        gate('TURNOVER_OI_RATIO', '1.6', '0.2', 'gt', 'ratio'),
        gate('SPREAD_PCT', '0.0002', '0.1', 'lt', 'pct'),
        gate('SLIPPAGE_50K_PCT', '0.004', '0.25', 'lt', 'pct'),
      ],
      funding: {normalized_8h_pct: '0.01', interval_hours: 8, risk: 'NORMAL'},
      heat_score: 92,
      rank: 1,
      reasons: ['All five gates pass; funding normal'],
    },
    {
      ...base,
      instrument_id: 'mock:perp:ETHUSD',
      display_symbol: 'ETHUSD',
      status: 'QUALIFIED',
      risk_tags: ['WATCH'],
      gates: [
        gate('TURNOVER_24H', '2986560000', '50000000', 'gt', 'USD'),
        gate('OPEN_INTEREST_USD', '1866600000', '10000000', 'gt', 'USD'),
        gate('TURNOVER_OI_RATIO', '1.6', '0.2', 'gt', 'ratio'),
        gate('SPREAD_PCT', '0.0006', '0.1', 'lt', 'pct'),
        gate('SLIPPAGE_50K_PCT', '0.011', '0.25', 'lt', 'pct'),
      ],
      funding: {normalized_8h_pct: '0.12', interval_hours: 8, risk: 'WATCH'},
      heat_score: 77,
      rank: 2,
      reasons: ['All five gates pass', 'Funding 0.12%/8h is at watch level'],
    },
    {
      ...base,
      instrument_id: 'mock:perp:SOLUSD',
      display_symbol: 'SOLUSD',
      status: 'INSUFFICIENT_DATA',
      risk_tags: [],
      gates: [
        gate('TURNOVER_24H', '2540160000', '50000000', 'gt', 'USD'),
        gate('OPEN_INTEREST_USD', '1587600000', '10000000', 'gt', 'USD'),
        gate('TURNOVER_OI_RATIO', '1.6', '0.2', 'gt', 'ratio'),
        gate('SPREAD_PCT', '0.0011', '0.1', 'lt', 'pct'),
        gate('SLIPPAGE_50K_PCT', null, '0.25', 'lt', 'pct'),
      ],
      funding: {normalized_8h_pct: null, interval_hours: null, risk: 'UNKNOWN'},
      heat_score: null,
      rank: null,
      reasons: ['Order book unavailable, so 50K slippage is unknown'],
    },
  ];
}

/** Options for the DEMO stream. */
export interface DemoStreamOptions {
  /** Clock. */
  now?: () => number;
  /** Tick interval. */
  intervalMs?: number;
}

/** Simulated market stream emitting DEMO candle updates. */
export class DemoMarketStream implements StreamClient {
  private sequence = 0;
  private readonly timers = new Set<ReturnType<typeof setInterval>>();
  private readonly now: () => number;

  /**
   * Creates the stream.
   * @param options Options.
   */
  constructor(private readonly options: DemoStreamOptions = {}) {
    this.now = options.now ?? (() => Date.now());
  }

  /**
   * Subscribes to channels; candles and feed health are simulated.
   * @param channels Specs.
   * @param handler Handler.
   * @return Unsubscribe.
   */
  subscribe(
    channels: readonly ChannelSpec[],
    handler: StreamHandler
  ): () => void {
    const local = new Set<ReturnType<typeof setInterval>>();
    const lastStart = new Map<string, number>();
    const tick = () => {
      const now = this.now();
      for (const spec of channels) {
        if (
          spec.channel === 'candles' &&
          spec.instrument_id &&
          spec.timeframe
        ) {
          let instrument: DemoInstrumentSpec;
          try {
            instrument = demoSpec(spec.instrument_id);
          } catch {
            continue;
          }
          const key = `${spec.instrument_id}:${spec.timeframe}`;
          const start = floorToTimeframe(spec.timeframe, now);
          const previous = lastStart.get(key);
          if (previous !== undefined && previous < start) {
            handler(
              this.envelope(
                'candle.updated',
                `candles:${key}`,
                demoCandle(instrument, spec.timeframe, previous, now)
              )
            );
          }
          lastStart.set(key, start);
          handler(
            this.envelope(
              'candle.updated',
              `candles:${key}`,
              demoCandle(instrument, spec.timeframe, start, now)
            )
          );
        } else if (spec.channel === 'feed_health') {
          handler(
            this.envelope('feed.health', 'feed_health', {
              venue: 'mock',
              state: 'HEALTHY',
              feed_mode: 'AUTO',
              feed_state: 'DEMO',
              transport: 'MOCK',
              last_message_ms: now,
              lag_ms: 0,
              reconnects: 0,
              gaps_detected: 0,
              last_error: null,
              as_of_ms: now,
            })
          );
        }
      }
    };
    const timer = setInterval(tick, this.options.intervalMs ?? 1000);
    local.add(timer);
    this.timers.add(timer);
    return () => {
      for (const t of local) {
        clearInterval(t);
        this.timers.delete(t);
      }
    };
  }

  /**
   * Builds an envelope.
   * @param type Event type.
   * @param channel Channel key.
   * @param payload Payload.
   * @return Envelope.
   */
  private envelope(
    type: StreamEnvelope['type'],
    channel: string,
    payload: object
  ): StreamEnvelope {
    this.sequence += 1;
    return {
      type,
      event_id: `demo-${this.sequence}`,
      schema_version: 1,
      sequence: this.sequence,
      event_ts_ms: this.now(),
      correlation_id: null,
      channel,
      payload: payload as Record<string, unknown>,
    };
  }

  /**
   * Reports a permanently open status.
   * @param handler Status handler.
   * @return Unsubscribe.
   */
  onStatus(handler: (status: StreamStatus) => void): () => void {
    handler('OPEN');
    return () => undefined;
  }

  /** Stops every timer. */
  close(): void {
    for (const timer of this.timers) clearInterval(timer);
    this.timers.clear();
  }
}
