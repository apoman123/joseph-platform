/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO implementation of `MarketDataClient` and a simulated
 * market stream for the synthetic instruments of the `mock` venue.
 *
 * Candles, snapshots and feed health carry `feed_state: 'DEMO'`. Requests
 * for any other venue's instruments fail instead of inventing data. Chart
 * studies run through `DemoStudyClient` (`demo-study-client`) over these
 * candles.
 */

import {
  Decimal,
  TIMEFRAMES,
  floorToTimeframe,
  isSecondsTimeframe,
  type BarCalendar,
  type CandleBatch,
  type CandleQuery,
  type ChannelSpec,
  type Instrument,
  type InstrumentList,
  type InstrumentQuery,
  type MarketDataClient,
  type MarketSnapshot,
  type OrderBook,
  type SessionList,
  type SessionQuery,
  type StreamClient,
  type StreamEnvelope,
  type StreamHandler,
  type StreamStatus,
  type VenueStatusList,
} from '@joseph/contracts';
import {demoCalendar, demoSessionList} from './demo-sessions';
import {
  DEMO_INSTRUMENT_SPECS,
  demoCandle,
  demoSeries,
  demoTickAt,
  priceAt,
  type DemoInstrumentSpec,
} from './prices';

/** Bars returned by `getCandles` without `fromMs` or `limit`. */
const DEFAULT_LIMIT = 400;

/**
 * Looks up a DEMO instrument.
 * @param instrumentId Canonical instrument id, e.g. `mock:perp:BTCUSD`.
 * @return The DEMO spec.
 * @throws `Error` for non-DEMO instruments (DEMO never fakes real venues).
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
 * Returns the session calendar of a DEMO instrument.
 * @param spec Instrument.
 * @return Calendar; continuous `24x7` when the spec names none (perpetuals).
 */
export function demoSpecCalendar(spec: DemoInstrumentSpec): BarCalendar {
  return demoCalendar(spec.calendar ?? '24x7');
}

/**
 * Builds the contract instrument for a DEMO spec. Perpetuals get a 5 USD
 * minimum notional, 8-hour funding and 20x maximum leverage; other market
 * types have none of these.
 * @param spec Instrument.
 * @param nowMs Current time in epoch ms (the record's `as_of_ms`).
 * @return Instrument.
 */
export function demoInstrument(
  spec: DemoInstrumentSpec,
  nowMs: number
): Instrument {
  const marketType = spec.marketType ?? 'perp';
  const perp = marketType === 'perp';
  const quote = spec.quote ?? 'USD';
  return {
    instrument_id: spec.instrumentId,
    key: {
      venue: 'mock',
      market_type: marketType,
      venue_market_id: spec.displaySymbol,
      canonical_base: spec.base,
      quote,
      settlement: quote,
      instrument_version: 1,
    },
    display_symbol: spec.displaySymbol,
    tick_size: spec.tickSize,
    lot_size: spec.lotSize,
    min_qty: spec.lotSize,
    max_qty: null,
    min_notional: perp ? '5' : null,
    contract_multiplier: '1',
    linear: true,
    status: 'TRADING',
    funding_interval_hours: perp ? 8 : null,
    max_leverage: perp ? '20' : null,
    asset_class: spec.assetClass ?? 'CRYPTO',
    source: 'mock',
    as_of_ms: nowMs,
    calendar: spec.calendar ?? '24x7',
    listing_exchange: spec.listingExchange ?? null,
    tick_ladder: [...(spec.tickLadder ?? [])],
    price_limit_pct: spec.priceLimitPct ?? null,
    board_lot: spec.boardLot ?? null,
    name: spec.name ?? null,
    local_name: spec.localName ?? null,
  };
}

/**
 * Returns the last traded DEMO price: the synthetic path now for 24x7
 * markets; for session markets, while closed, the price 1 ms before the
 * latest session's close. Rounded to the tick (the ladder rung at that
 * price).
 * @param spec Instrument.
 * @param nowMs Current time in epoch ms.
 * @return Price and the epoch ms it traded at.
 */
export function demoLastPrice(
  spec: DemoInstrumentSpec,
  nowMs: number
): {price: Decimal; tradeMs: number} {
  const calendar = demoSpecCalendar(spec);
  const tradeMs = calendar.continuous
    ? nowMs
    : Math.min(nowMs, calendar.endOf('1d', calendar.floor('1d', nowMs)) - 1);
  const raw = priceAt(spec, tradeMs);
  const tick = Decimal.parse(demoTickAt(spec, raw));
  return {price: Decimal.fromNumber(raw, 10).roundToStep(tick), tradeMs};
}

/**
 * Returns whether a DEMO spec matches search text (symbol, base, English or
 * local name).
 * @param spec Instrument.
 * @param text Search text, a substring in any case (Chinese names match as
 *     typed).
 * @return True on a match; blank text matches every spec.
 */
export function demoSpecMatches(
  spec: DemoInstrumentSpec,
  text: string
): boolean {
  const q = text.trim().toUpperCase();
  if (!q) return true;
  return [spec.displaySymbol, spec.base, spec.name ?? '', spec.localName ?? '']
    .map(value => value.toUpperCase())
    .some(value => value.includes(q));
}

/** Options for the DEMO client. */
export interface DemoMarketOptions {
  /** Clock in epoch ms (injectable for tests; defaults to `Date.now`). */
  now?: () => number;
}

/**
 * DEMO `MarketDataClient`: serves synthetic data for `mock` instruments
 * only; calls for any other instrument reject.
 */
export class DemoMarketDataClient implements MarketDataClient {
  private readonly now: () => number;

  /**
   * Creates the client.
   * @param options Injectable clock.
   */
  constructor(options: DemoMarketOptions = {}) {
    this.now = options.now ?? (() => Date.now());
  }

  /**
   * Lists the DEMO venue.
   * @return Status of the single `mock` venue: its capabilities and a
   *     healthy DEMO feed.
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
            market_types: ['perp', 'stock', 'etf', 'rate'],
            market_data: {
              rest_history: true,
              native_ws_candles: true,
              native_ws_trades: false,
              native_ws_book: false,
              book_snapshot: true,
              open_interest: true,
              funding: true,
              trades_history: false,
              taker_volume: true,
              classified_delta: true,
            },
            timeframes: [...TIMEFRAMES],
            trade_built_timeframes: TIMEFRAMES.filter(isSecondsTimeframe),
            calendar: '24x7',
            data_delay_ms: null,
            requires_credentials: false,
            credentials_configured: true,
            notes: [
              'Synthetic data generated in the browser. Not a real market.',
              'Seconds bars stand in for bars the engine builds from public trades.',
              'Demo stocks, the bond ETFs and the Treasury yield follow their exchange session calendars (XNYS, XTAI, USBOND).',
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
   * @param query Venue, market type and search-text filters; `limit`
   *     defaults to 500.
   * @return Matching instruments; none for any venue other than `mock`.
   */
  async listInstruments(query: InstrumentQuery): Promise<InstrumentList> {
    const now = this.now();
    const venueOk = !query.venue || query.venue === 'mock';
    const instruments = venueOk
      ? DEMO_INSTRUMENT_SPECS.filter(
          spec =>
            (!query.marketType ||
              (spec.marketType ?? 'perp') === query.marketType) &&
            demoSpecMatches(spec, query.q ?? '')
        )
          .slice(0, query.limit ?? 500)
          .map(spec => demoInstrument(spec, now))
      : [];
    return {instruments, as_of_ms: now};
  }

  /**
   * Builds DEMO candles.
   * @param query Series and range; `toMs` is exclusive, so a bar that
   *     starts at it is left out. With `fromMs`: the bars from the one
   *     containing it up to `toMs` (1 to 2000 bars). Without: the last
   *     `limit` bars (default 400, at most 2000) before `toMs`. Bars never
   *     go past the one forming now, which ends the series when `toMs` is
   *     omitted.
   * @return Candle batch, oldest first; always complete, without gaps.
   * @throws `Error` when the instrument is not a DEMO instrument.
   */
  async getCandles(query: CandleQuery): Promise<CandleBatch> {
    const spec = demoSpec(query.instrumentId);
    const calendar = demoSpecCalendar(spec);
    const now = this.now();
    // Exclusive end; `now + 1` keeps the bar forming now.
    const toMs = Math.min(query.toMs ?? Infinity, now + 1);
    const limit =
      query.fromMs !== undefined
        ? Math.min(
            2000,
            Math.max(
              1,
              calendar.barsBetween(
                query.timeframe,
                calendar.floor(query.timeframe, query.fromMs),
                toMs
              )
            )
          )
        : Math.min(2000, query.limit ?? DEFAULT_LIMIT);
    return {
      schema_version: 1,
      instrument_id: spec.instrumentId,
      timeframe: query.timeframe,
      candles: demoSeries(spec, query.timeframe, limit, now, toMs, calendar),
      feed_state: 'DEMO',
      source: 'mock',
      as_of_ms: now,
      complete: true,
      gaps: [],
    };
  }

  /**
   * Builds a DEMO snapshot.
   * @param instrumentId DEMO instrument id.
   * @return Snapshot: last, mark and index equal; bid and ask one tick
   *     either side; the 24-hour change measured from the path 24 hours ago
   *     (24x7) or just before the latest session opened; funding (a fixed
   *     0.01% per 8 hours) and open interest for perpetuals only.
   * @throws `Error` when the instrument is not a DEMO instrument.
   */
  async getSnapshot(instrumentId: string): Promise<MarketSnapshot> {
    const spec = demoSpec(instrumentId);
    const now = this.now();
    const calendar = demoSpecCalendar(spec);
    // Session markets keep the last price of the latest session while
    // closed. Their change is measured from the path 1 ms before that
    // session's daily bar opened, standing in for the previous close.
    const {price: last, tradeMs} = demoLastPrice(spec, now);
    const tick = Decimal.parse(demoTickAt(spec, last.toNumber()));
    const reference = calendar.continuous
      ? priceAt(spec, now - 86_400_000)
      : priceAt(spec, calendar.floor('1d', tradeMs) - 1);
    const change = ((last.toNumber() - reference) / reference) * 100;
    const perp = (spec.marketType ?? 'perp') === 'perp';
    // A day is 1440 minutes on 24x7 markets and a 390-minute (6.5-hour)
    // session elsewhere.
    const volumeDay = spec.volumePerMinute * (calendar.continuous ? 1440 : 390);
    return {
      instrument_id: instrumentId,
      last: last.toString(),
      mark: last.toString(),
      index: last.toString(),
      best_bid: last.sub(tick).toString(),
      best_ask: last.add(tick).toString(),
      spread_pct: tick.add(tick).div(last).mul(Decimal.parse('100')).toFixed(6),
      turnover_24h_quote: Decimal.fromNumber(
        volumeDay * spec.basePrice,
        0
      ).toString(),
      volume_24h_base: Decimal.fromNumber(volumeDay, 0).toString(),
      change_24h_pct: Decimal.fromNumber(change, 4).toString(),
      funding: !perp
        ? null
        : {
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
      open_interest: !perp
        ? null
        : {
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
   * @param instrumentId DEMO instrument id.
   * @param depth Price levels per side (default 20).
   * @return Book with levels one tick apart from the snapshot's best bid and
   *     ask; sizes start at 5% of the per-minute volume and grow 40% of that
   *     per level.
   * @throws `Error` when the instrument is not a DEMO instrument.
   */
  async getOrderBook(instrumentId: string, depth = 20): Promise<OrderBook> {
    const snapshot = await this.getSnapshot(instrumentId);
    const spec = demoSpec(instrumentId);
    const tick = Decimal.parse(
      demoTickAt(spec, Number(snapshot.last ?? spec.basePrice))
    );
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
   * Lists DEMO sessions of an exchange calendar (the engine's
   * `/v1/calendars/{calendar_id}/sessions`).
   * @param query Calendar and the half-open window `[fromMs, toMs)` in epoch
   *     ms.
   * @return Sessions overlapping the window, oldest first.
   */
  async getSessions(query: SessionQuery): Promise<SessionList> {
    return demoSessionList(query);
  }
}

/** Options for the DEMO stream. */
export interface DemoStreamOptions {
  /** Clock in epoch ms (defaults to `Date.now`). */
  now?: () => number;
  /** Milliseconds between simulated updates (default 1000). */
  intervalMs?: number;
}

/**
 * Simulated market stream emitting DEMO candle updates. Every interval it
 * re-sends each subscribed series' forming bar; when a new bar starts, it
 * first sends the previous bar once more, now final.
 */
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
   * @param channels Channel specs. `candles` (with instrument and timeframe)
   *     and `feed_health` are served; candles of non-DEMO instruments and
   *     other channels are ignored.
   * @param handler Receives each envelope.
   * @return Unsubscribe function that stops this subscription's timer.
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
            // Only DEMO instruments have synthetic bars: skip the rest.
            continue;
          }
          const key = `${spec.instrument_id}:${spec.timeframe}`;
          const calendar = demoSpecCalendar(instrument);
          // Between sessions this is the last (final) bar of the session.
          const start = calendar.floor(spec.timeframe, now);
          const previous = lastStart.get(key);
          if (previous !== undefined && previous < start) {
            handler(
              this.envelope(
                'candle.updated',
                `candles:${key}`,
                demoCandle(instrument, spec.timeframe, previous, now, calendar)
              )
            );
          }
          lastStart.set(key, start);
          handler(
            this.envelope(
              'candle.updated',
              `candles:${key}`,
              demoCandle(instrument, spec.timeframe, start, now, calendar)
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
   * Builds an envelope with the next sequence number.
   * @param type Event type.
   * @param channel Channel key, e.g. `candles:<instrument>:<timeframe>`.
   * @param payload Candle or feed-health record.
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
      payload,
    };
  }

  /**
   * Reports `OPEN` once, synchronously: the DEMO stream never disconnects.
   * @param handler Status handler.
   * @return No-op unsubscribe function.
   */
  onStatus(handler: (status: StreamStatus) => void): () => void {
    handler('OPEN');
    return () => undefined;
  }

  /** Stops the timers of every subscription. */
  close(): void {
    for (const timer of this.timers) {
      clearInterval(timer);
    }
    this.timers.clear();
  }
}
