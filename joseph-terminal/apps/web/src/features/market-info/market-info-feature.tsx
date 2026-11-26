/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Market panel: snapshot (last, mark, index, bid/ask,
 * funding, open interest) and contract specs for the active instrument.
 * Securities show their listing exchange, currency, board lot, price limit
 * and session hours instead of perpetual fields; a Treasury yield shows
 * the yield in percent and its publication session.
 */

import {
  type Candle,
  EXCHANGE_HOURS,
  type Instrument,
  instrumentCalendarId,
  isExchangeCalendar,
  type MarketSnapshot,
  parseInstrumentId,
  VENUE_NAMES,
} from '@joseph/contracts';
import {useEffect, useState} from 'react';
import {useStore, type WebFeature} from '../../shell/react-bindings';
import {
  formatAge,
  formatDecimal,
  formatInstrumentPrice,
  formatPct,
  formatPrice,
  formatUsdCompact,
  formatVolume,
} from '../../shell/format';
import {marketTypeLabel} from '../../shell/markets';
import {shellManifest} from '../shell/manifest';

/** Snapshot refresh interval in milliseconds. */
const REFRESH_MS = 5_000;

/** Panel data. */
interface MarketData {
  /** Latest snapshot, or null while loading or when none arrived. */
  snapshot: MarketSnapshot | null;
  /** Registry entry, or null while loading or when it is not listed. */
  instrument: Instrument | null;
  /** Message of the last failed snapshot request, or null. */
  error: string | null;
}

/**
 * Renders a definition-list row. Values use tabular figures unless `plain`;
 * `tone` adds a colour class such as `j-pos`.
 * @param props Label, value, tone and plain flag.
 * @return Row.
 */
function Row({
  label,
  value,
  tone,
  plain,
}: {
  label: string;
  value: string;
  tone?: string;
  plain?: boolean;
}) {
  return (
    <div className="kv__row">
      <dt>{label}</dt>
      <dd className={`${plain ? '' : 'j-tabular'} ${tone ?? ''}`}>{value}</dd>
    </div>
  );
}

/**
 * Formats minutes after midnight as `HH:MM`.
 * @param minutes Minutes after midnight.
 * @return Text.
 */
function clock(minutes: number): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
}

/**
 * Formats a large amount compactly in its currency (`$106.70M`,
 * `NT$2.31B`, or `1.20M EUR`).
 * @param value Decimal string.
 * @param currency Quote currency.
 * @return Text, or `—` when the value is missing or invalid.
 */
export function formatMoneyCompact(
  value: string | null | undefined,
  currency: string
): string {
  const usd = formatUsdCompact(value);
  if (usd === '—' || currency === 'USD') return usd;
  if (currency === 'TWD') return `NT${usd}`;
  return `${usd.slice(1)} ${currency}`;
}

/**
 * Describes a tick ladder, e.g. `0.01 below 10, 0.05 to 50, …`.
 * @param instrument Instrument.
 * @return Text, or the plain tick size without a ladder.
 */
export function tickDescription(instrument: Instrument): string {
  // `Number` only orders the steps; the text keeps the decimal strings.
  const ladder = [...(instrument.tick_ladder ?? [])].sort(
    (a, b) => Number(a.min_price) - Number(b.min_price)
  );
  if (ladder.length === 0) return instrument.tick_size;
  return ladder
    .map((step, i) => {
      const next = ladder[i + 1];
      return next
        ? `${step.tick_size} below ${next.min_price}`
        : `${step.tick_size} from ${step.min_price}`;
    })
    .join(', ');
}

/**
 * Creates the market info feature.
 * @return Feature contributing the side-panel market view.
 */
export function marketInfoFeature(): WebFeature {
  return {
    manifest: shellManifest(
      'market-info',
      'Market',
      'Snapshot and contract specs for the active instrument.',
      {
        uiSlots: ['side-panel'],
        requiredCapabilities: ['market'],
        permissions: ['market:read'],
      }
    ),
    activate(ctx) {
      /**
       * Renders the side-panel snapshot and specs of the selected instrument.
       * @return Panel.
       */
      function MarketPanel() {
        const selection = useStore(ctx.selection);
        const [data, setData] = useState<MarketData>({
          snapshot: null,
          instrument: null,
          error: null,
        });
        useEffect(() => {
          let alive = true;
          const {venue, venueMarketId, marketType} = parseInstrumentId(
            selection.instrumentId
          );
          setData({snapshot: null, instrument: null, error: null});
          // A failed registry lookup only leaves the specs as dashes.
          ctx.clients.market
            .listInstruments({venue, q: venueMarketId})
            .then(list => {
              if (alive) {
                const instrument =
                  list.instruments.find(
                    i => i.instrument_id === selection.instrumentId
                  ) ?? null;
                setData(d => ({...d, instrument}));
              }
            })
            .catch(() => undefined);
          const load = () =>
            ctx.clients.market
              .getSnapshot(selection.instrumentId)
              .then(
                snapshot =>
                  alive &&
                  void setData(d =>
                    d.snapshot && snapshot.as_of_ms < d.snapshot.as_of_ms
                      ? d
                      : {...d, snapshot, error: null}
                  )
              )
              .catch(
                (error: unknown) =>
                  alive &&
                  void setData(d => ({
                    ...d,
                    error:
                      error instanceof Error ? error.message : 'Request failed',
                  }))
              );
          void load();
          const unsubscribe =
            marketType === 'rate'
              ? () => undefined
              : ctx.clients.marketStream.subscribe(
                  [
                    {
                      channel: 'candles',
                      instrument_id: selection.instrumentId,
                      timeframe: '1m',
                      feed_mode: 'AUTO',
                    },
                  ],
                  envelope => {
                    if (!alive || envelope.type !== 'candle.updated') return;
                    const candle = envelope.payload as unknown as Candle;
                    if (candle.instrument_id !== selection.instrumentId) return;
                    setData(current => {
                      const snapshot = current.snapshot;
                      if (
                        !snapshot ||
                        candle.received_at_ms < snapshot.as_of_ms
                      ) {
                        return current;
                      }
                      return {
                        ...current,
                        snapshot: {
                          ...snapshot,
                          last: candle.close,
                          as_of_ms: candle.received_at_ms,
                          feed_state: candle.feed_state,
                          source: candle.source,
                          data_age_ms: 0,
                          stale: false,
                        },
                      };
                    });
                  }
                );
          const timer = setInterval(() => void load(), REFRESH_MS);
          return () => {
            alive = false;
            clearInterval(timer);
            unsubscribe();
          };
        }, [selection.instrumentId]);
        const {snapshot, instrument, error} = data;
        const tick = instrument?.tick_size ?? null;
        const {venue, marketType} = parseInstrumentId(selection.instrumentId);
        const perp = marketType === 'perp';
        const rate = marketType === 'rate';
        const currency = instrument?.key.quote ?? 'USD';
        const price = (value: string | null | undefined) =>
          instrument
            ? formatInstrumentPrice(value, instrument)
            : formatPrice(value, tick);
        const calendarId = instrumentCalendarId(
          selection.instrumentId,
          instrument
        );
        const hours = isExchangeCalendar(calendarId)
          ? EXCHANGE_HOURS[calendarId]
          : null;
        const names = [instrument?.name, instrument?.local_name]
          .filter(Boolean)
          .join(' · ');
        return (
          <div className="market-panel">
            <header className="market-panel__head">
              <div>
                <h2>
                  {instrument?.display_symbol ??
                    parseInstrumentId(selection.instrumentId).venueMarketId}
                </h2>
                <p className="j-muted">
                  {VENUE_NAMES[venue]},{' '}
                  {marketTypeLabel(marketType).toLowerCase()}
                </p>
                {names && (
                  <p className="market-panel__name" data-testid="market-name">
                    {names}
                  </p>
                )}
              </div>
              {snapshot && (
                <span
                  className={`j-badge j-badge--${snapshot.stale ? 'stale' : snapshot.feed_state.toLowerCase()}`}
                >
                  {snapshot.stale
                    ? `Stale ${formatAge(snapshot.data_age_ms)}`
                    : snapshot.feed_state === 'DEMO'
                      ? 'Demo data'
                      : snapshot.feed_state}
                </span>
              )}
            </header>
            {error && (
              <div className="j-notice j-notice--danger" role="alert">
                Couldn’t load the market snapshot: {error}
              </div>
            )}
            <dl className="kv">
              <Row
                label={rate ? 'Yield' : 'Last'}
                value={price(snapshot?.last)}
              />
              {perp && (
                <>
                  <Row label="Mark" value={price(snapshot?.mark)} />
                  <Row label="Index" value={price(snapshot?.index)} />
                </>
              )}
              {!rate && (
                <>
                  <Row
                    label="Bid"
                    value={price(snapshot?.best_bid)}
                    tone="j-pos"
                  />
                  <Row
                    label="Ask"
                    value={price(snapshot?.best_ask)}
                    tone="j-neg"
                  />
                  <Row
                    label="Spread"
                    value={
                      snapshot?.spread_pct
                        ? `${formatDecimal(snapshot.spread_pct, 4)}%`
                        : '—'
                    }
                  />
                </>
              )}
              <Row
                label={perp ? '24h change' : 'Change'}
                value={formatPct(snapshot?.change_24h_pct)}
                tone={
                  snapshot?.change_24h_pct?.startsWith('-') ? 'j-neg' : 'j-pos'
                }
              />
              {!rate && (
                <Row
                  label={perp ? '24h turnover' : 'Turnover'}
                  value={formatMoneyCompact(
                    snapshot?.turnover_24h_quote,
                    currency
                  )}
                />
              )}
              {!rate && !perp && (
                <Row
                  label="Volume"
                  value={formatVolume(
                    snapshot?.volume_24h_base == null
                      ? null
                      : Number(snapshot.volume_24h_base),
                    instrument
                  )}
                />
              )}
              {perp && (
                <>
                  <Row
                    label="Open interest"
                    value={formatUsdCompact(
                      snapshot?.open_interest?.notional_usd
                    )}
                  />
                  <Row
                    label="Funding (8h)"
                    value={
                      snapshot?.funding
                        ? `${formatDecimal(snapshot.funding.normalized_8h_pct, 4)}%, ${snapshot.funding.kind.toLowerCase()}`
                        : '—'
                    }
                  />
                </>
              )}
            </dl>
            <h3 className="market-panel__sub">
              {perp ? 'Contract' : rate ? 'Series' : 'Security'}
            </h3>
            <dl className="kv">
              <Row label="Instrument id" value={selection.instrumentId} plain />
              {instrument?.listing_exchange && (
                <Row
                  label="Listed on"
                  value={instrument.listing_exchange}
                  plain
                />
              )}
              {!perp && (
                <Row
                  label={rate ? 'Unit' : 'Currency'}
                  value={rate ? 'percent' : currency}
                  plain
                />
              )}
              {hours && (
                <Row
                  label="Session"
                  value={`${clock(hours.openMinute)}–${clock(hours.closeMinute)} ${hours.timeZone} (${calendarId})`}
                  plain
                />
              )}
              <Row
                label="Tick size"
                value={instrument ? tickDescription(instrument) : '—'}
              />
              {!rate && (
                <Row label="Lot size" value={instrument?.lot_size ?? '—'} />
              )}
              {instrument?.board_lot && (
                <Row
                  label="Board lot"
                  value={`${formatDecimal(instrument.board_lot, 0)} shares (1 張)`}
                />
              )}
              {instrument?.price_limit_pct && (
                <Row
                  label="Daily price limit"
                  value={`±${instrument.price_limit_pct}% of the reference price`}
                />
              )}
              {perp && (
                <>
                  <Row
                    label="Min notional"
                    value={instrument?.min_notional ?? '—'}
                  />
                  <Row
                    label="Max leverage"
                    value={
                      instrument?.max_leverage
                        ? `${instrument.max_leverage}x`
                        : '—'
                    }
                  />
                  <Row
                    label="Funding interval"
                    value={
                      instrument?.funding_interval_hours
                        ? `${instrument.funding_interval_hours} h`
                        : '—'
                    }
                  />
                </>
              )}
              <Row
                label="Status"
                value={
                  rate
                    ? 'published rate, not tradable'
                    : (instrument?.status.toLowerCase() ?? '—')
                }
              />
            </dl>
          </div>
        );
      }
      ctx.slots.contribute('side-panel', {
        id: 'market-info',
        title: 'Market',
        order: 5,
        icon: 'info',
        view: MarketPanel,
      });
      return () => undefined;
    },
  };
}
