/**
 * @fileoverview Market panel: snapshot (last, mark, index, bid/ask,
 * funding, open interest) and contract specs for the active instrument.
 */
import type {Instrument, MarketSnapshot} from '@joseph/contracts';
import {parseInstrumentId, VENUE_NAMES} from '@joseph/contracts';
import {useEffect, useState} from 'react';
import type {TerminalRuntime} from '../../composition/runtime';
import {
  useStore,
  type WebFeature,
  type WebSlotProps,
} from '../../shell/react-bindings';
import {
  formatAge,
  formatDecimal,
  formatPct,
  formatPrice,
  formatUsdCompact,
} from '../../shell/format';
import {shellManifest} from '../shell/manifest';

/** Snapshot refresh interval. */
const REFRESH_MS = 5_000;

/** Panel data. */
interface MarketData {
  /** Snapshot. */
  snapshot: MarketSnapshot | null;
  /** Instrument specs. */
  instrument: Instrument | null;
  /** Error. */
  error: string | null;
}

/**
 * Definition-list row.
 * @param props Label and value.
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
 * Market info feature.
 * @param _runtime Runtime (unused).
 * @return Feature.
 */
export function marketInfoFeature(_runtime: TerminalRuntime): WebFeature {
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
       * Side panel view.
       * @param _props Slot props (unused).
       * @return Panel.
       */
      function MarketPanel(_props: WebSlotProps) {
        const selection = useStore(ctx.selection);
        const [data, setData] = useState<MarketData>({
          snapshot: null,
          instrument: null,
          error: null,
        });
        useEffect(() => {
          let alive = true;
          const {venue, venueMarketId} = parseInstrumentId(
            selection.instrumentId
          );
          setData({snapshot: null, instrument: null, error: null});
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
                  alive && setData(d => ({...d, snapshot, error: null}))
              )
              .catch(
                (error: unknown) =>
                  alive &&
                  setData(d => ({
                    ...d,
                    error:
                      error instanceof Error ? error.message : 'Request failed',
                  }))
              );
          void load();
          const timer = setInterval(() => void load(), REFRESH_MS);
          return () => {
            alive = false;
            clearInterval(timer);
          };
        }, [selection.instrumentId]);
        const {snapshot, instrument, error} = data;
        const tick = instrument?.tick_size ?? null;
        const {venue} = parseInstrumentId(selection.instrumentId);
        return (
          <div className="market-panel">
            <header className="market-panel__head">
              <div>
                <h2>
                  {instrument?.display_symbol ??
                    parseInstrumentId(selection.instrumentId).venueMarketId}
                </h2>
                <p className="j-muted">{VENUE_NAMES[venue]}, perpetual</p>
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
              <Row label="Last" value={formatPrice(snapshot?.last, tick)} />
              <Row label="Mark" value={formatPrice(snapshot?.mark, tick)} />
              <Row label="Index" value={formatPrice(snapshot?.index, tick)} />
              <Row
                label="Bid"
                value={formatPrice(snapshot?.best_bid, tick)}
                tone="j-pos"
              />
              <Row
                label="Ask"
                value={formatPrice(snapshot?.best_ask, tick)}
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
              <Row
                label="24h change"
                value={formatPct(snapshot?.change_24h_pct)}
                tone={
                  snapshot?.change_24h_pct?.startsWith('-') ? 'j-neg' : 'j-pos'
                }
              />
              <Row
                label="24h turnover"
                value={formatUsdCompact(snapshot?.turnover_24h_quote)}
              />
              <Row
                label="Open interest"
                value={formatUsdCompact(snapshot?.open_interest?.notional_usd)}
              />
              <Row
                label="Funding (8h)"
                value={
                  snapshot?.funding
                    ? `${formatDecimal(snapshot.funding.normalized_8h_pct, 4)}%, ${snapshot.funding.kind.toLowerCase()}`
                    : '—'
                }
              />
            </dl>
            <h3 className="market-panel__sub">Contract</h3>
            <dl className="kv">
              <Row label="Instrument id" value={selection.instrumentId} plain />
              <Row label="Tick size" value={instrument?.tick_size ?? '—'} />
              <Row label="Lot size" value={instrument?.lot_size ?? '—'} />
              <Row
                label="Min notional"
                value={instrument?.min_notional ?? '—'}
              />
              <Row
                label="Max leverage"
                value={
                  instrument?.max_leverage ? `${instrument.max_leverage}x` : '—'
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
              <Row
                label="Status"
                value={instrument?.status.toLowerCase() ?? '—'}
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
