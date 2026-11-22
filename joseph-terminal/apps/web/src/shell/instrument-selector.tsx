/**
 * @fileoverview Instrument selector: Venue, then exact venue instrument.
 *
 * Results always show the canonical venue-scoped id, so `BTCUSDT` on
 * Binance is never confused with `BTC` on Hyperliquid.
 */
'use client';
import {
  VENUE_NAMES,
  parseInstrumentId,
  type Instrument,
  type InstrumentId,
  type Venue,
} from '@joseph/contracts';
import {ChevronDown, Search} from 'lucide-react';
import {useEffect, useRef, useState, type ReactElement} from 'react';
import {Popover} from './popover';
import {useRuntime} from './runtime-context';

/**
 * Instrument selector for the active chart.
 * @param props Current instrument and change handler.
 * @return Selector.
 */
export function InstrumentSelector({
  instrumentId,
  onSelect,
}: {
  instrumentId: InstrumentId;
  onSelect: (id: InstrumentId) => void;
}): ReactElement {
  const runtime = useRuntime();
  const current = parseInstrumentId(instrumentId);
  return (
    <Popover
      ariaLabel="Choose instrument"
      testId="instrument-selector"
      className="instrument-trigger"
      label={
        <>
          <span className="instrument-trigger__symbol">
            {current.venueMarketId}
          </span>
          <span className="instrument-trigger__venue">
            {VENUE_NAMES[current.venue]}
          </span>
          <span className="sr-only">, change instrument</span>
          <ChevronDown size={14} aria-hidden="true" />
        </>
      }
    >
      {close => (
        <InstrumentSearch
          initialVenue={current.venue}
          demo={runtime.clients.demo}
          onPick={id => {
            onSelect(id);
            close();
          }}
        />
      )}
    </Popover>
  );
}

/**
 * Search body.
 * @param props Initial venue, mode and pick handler.
 * @return Search UI.
 */
function InstrumentSearch({
  initialVenue,
  demo,
  onPick,
}: {
  initialVenue: Venue;
  demo: boolean;
  onPick: (id: InstrumentId) => void;
}) {
  const runtime = useRuntime();
  const [venues, setVenues] = useState<Venue[]>([initialVenue]);
  const [venue, setVenue] = useState<Venue | 'all'>(
    demo ? 'mock' : initialVenue
  );
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Instrument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  useEffect(() => {
    runtime.clients.market
      .listVenues()
      .then(list => {
        const available = list.venues.map(v => v.capabilities.venue);
        setVenues(available);
        // The chart's venue may not be enabled on this backend; searching it
        // would only fail, so fall back to every available venue.
        setVenue(current =>
          current === 'all' || available.includes(current) ? current : 'all'
        );
      })
      .catch(() => undefined);
  }, [runtime]);
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      runtime.clients.market
        .listInstruments({
          venue: venue === 'all' ? undefined : venue,
          marketType: 'perp',
          q: query || undefined,
        })
        .then(list => {
          if (!alive) return;
          setResults(list.instruments.slice(0, 50));
          setError(null);
        })
        .catch(
          (e: unknown) =>
            alive && setError(e instanceof Error ? e.message : 'Search failed')
        );
    }, 150);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [runtime, venue, query]);
  return (
    <div className="instrument-search">
      <div className="instrument-search__row">
        <label className="sr-only" htmlFor="venue-filter">
          Venue
        </label>
        <select
          id="venue-filter"
          className="j-select"
          value={venue}
          onChange={e => setVenue(e.target.value as Venue | 'all')}
        >
          {!demo && <option value="all">All venues</option>}
          {venues.map(v => (
            <option key={v} value={v}>
              {VENUE_NAMES[v]}
            </option>
          ))}
        </select>
      </div>
      <div className="instrument-search__field">
        <Search size={14} aria-hidden="true" />
        <input
          ref={input}
          className="j-input"
          placeholder="Search symbol, e.g. BTC"
          aria-label="Search instruments"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
      </div>
      {demo && (
        <p className="j-field__hint">
          Demo mode lists the synthetic demo venue only. Connect a market engine
          for real venues.
        </p>
      )}
      {error && (
        <p className="j-field__error" role="alert">
          {error}
        </p>
      )}
      <ul
        className="instrument-search__results"
        role="listbox"
        aria-label="Instruments"
      >
        {(results ?? []).map(item => (
          <li key={item.instrument_id}>
            <button
              type="button"
              role="option"
              aria-selected={false}
              className="j-menu__item instrument-option"
              onClick={() => onPick(item.instrument_id)}
            >
              <span className="instrument-option__symbol">
                {item.display_symbol}
              </span>
              <span className="instrument-option__id">
                {item.instrument_id}
              </span>
              <span className="instrument-option__meta">
                {item.key.settlement}-settled
              </span>
            </button>
          </li>
        ))}
        {results && results.length === 0 && (
          <li className="j-muted instrument-search__empty">
            No perpetuals match “{query}”.
          </li>
        )}
      </ul>
    </div>
  );
}
