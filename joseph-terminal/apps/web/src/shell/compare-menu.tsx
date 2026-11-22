/**
 * @fileoverview Compare menu: overlay up to three other exact-venue
 * instruments on the active chart, each on a shared left percentage axis.
 */
'use client';
import {
  VENUE_NAMES,
  parseInstrumentId,
  type ChartState,
  type Instrument,
} from '@joseph/contracts';
import {MAX_COMPARE} from '@joseph/terminal-shell';
import {X} from 'lucide-react';
import {useEffect, useState, type ReactElement} from 'react';
import {useRuntime} from './runtime-context';

/** Compare menu props. */
export interface CompareMenuProps {
  /** Active chart. */
  chart: ChartState;
}

/**
 * Compare menu body.
 * @param props Props.
 * @return Menu content.
 */
export function CompareMenu({chart}: CompareMenuProps): ReactElement {
  const runtime = useRuntime();
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Instrument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const current: readonly string[] = chart.compare ?? [];
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      return;
    }
    let alive = true;
    const timer = setTimeout(() => {
      runtime.clients.market.listInstruments({q}).then(
        list => {
          if (!alive) return;
          setResults(list.instruments.slice(0, 10));
          setError(null);
        },
        (failure: unknown) => {
          if (!alive) return;
          setResults([]);
          setError(
            failure instanceof Error ? failure.message : 'Search failed'
          );
        }
      );
    }, 180);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [query, runtime]);
  const set = (ids: readonly string[]) =>
    runtime.dispatch({
      type: 'setCompare',
      chartId: chart.chart_id,
      instrumentIds: ids,
    });
  const full = current.length >= MAX_COMPARE;
  return (
    <div className="compare-menu">
      <p className="layers-menu__note">
        Compared instruments share a percentage axis on the left, measured from
        the first visible bar.
      </p>
      {current.length > 0 && (
        <ul className="compare-menu__list" aria-label="Compared instruments">
          {current.map(id => {
            const {venue, venueMarketId} = parseInstrumentId(id);
            return (
              <li key={id}>
                <span>
                  {venueMarketId}{' '}
                  <span className="j-muted">{VENUE_NAMES[venue]}</span>
                </span>
                <button
                  type="button"
                  className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                  aria-label={`Stop comparing ${venueMarketId}`}
                  onClick={() => set(current.filter(x => x !== id))}
                >
                  <X size={13} aria-hidden="true" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <label className="j-label" htmlFor="compare-search">
        Add a symbol
      </label>
      <input
        id="compare-search"
        className="j-input"
        placeholder={full ? `Up to ${MAX_COMPARE} symbols` : 'Search ETH, SOL'}
        value={query}
        disabled={full}
        autoComplete="off"
        onChange={event => setQuery(event.target.value)}
      />
      {error && <p className="j-notice j-notice--danger">{error}</p>}
      {results.length > 0 && (
        <ul
          className="compare-menu__results"
          role="listbox"
          aria-label="Symbols"
        >
          {results.map(instrument => {
            const same = instrument.instrument_id === chart.instrument_id;
            const added = current.includes(instrument.instrument_id);
            return (
              <li
                key={instrument.instrument_id}
                role="option"
                aria-selected={added}
              >
                <button
                  type="button"
                  className="j-menu__item"
                  disabled={same || added || full}
                  data-testid={`compare-add-${instrument.instrument_id}`}
                  onClick={() => {
                    set([...current, instrument.instrument_id]);
                    setQuery('');
                  }}
                >
                  <span>{instrument.display_symbol}</span>
                  <span className="j-muted">
                    {
                      VENUE_NAMES[
                        parseInstrumentId(instrument.instrument_id).venue
                      ]
                    }
                    {same ? ' · this chart' : added ? ' · added' : ''}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
