/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Instrument picker: asset-class groups (Crypto, Taiwan
 * stocks, US stocks, Bonds & rates), venue filter and search by symbol or
 * name — including Chinese names on TWSE and TPEx — over exact venue
 * instruments.
 *
 * Results always show the canonical venue-scoped id, so `BTCUSDT` on
 * Binance is never confused with `BTC` on Hyperliquid. Rows carry the venue
 * and listing-exchange badges and say when a venue's data is delayed or
 * its data source has no API key configured.
 *
 * The search box takes focus when the picker opens. Groups are a
 * `role="tablist"`; results are a `role="listbox"` of option buttons,
 * sectioned by group.
 */

'use client';
import {
  VENUE_NAMES,
  parseInstrumentId,
  type Instrument,
  type InstrumentId,
  type Venue,
  type VenueCapabilities,
} from '@joseph/contracts';
import {ChevronDown, Search} from 'lucide-react';
import {useEffect, useMemo, useRef, useState, type ReactElement} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {
  GROUP_VENUES,
  MARKET_GROUPS,
  MARKET_GROUP_LABELS,
  marketGroupOf,
  marketTypeLabel,
  searchRank,
  venueAccess,
  type MarketGroup,
} from './markets';
import {Popover} from './popover';
import {useRuntime} from './runtime-context';
import {loadVenueCapabilities} from './venue-capabilities';

/** Maximum number of results shown. */
const MAX_RESULTS = 60;

/** Venues whose listings are searched by name in the browser. */
const NAMED_VENUES: ReadonlySet<Venue> = new Set<Venue>([
  'twse',
  'tpex',
  'nasdaq',
  'nyse',
  'cboe',
  'treasury',
  'mock',
]);

/** Short venue badges on result rows. */
const VENUE_BADGES: Readonly<Record<Venue, string>> = {
  binance: 'BIN',
  hyperliquid: 'HL',
  lighter: 'LTR',
  bitget: 'BG',
  mexc: 'MEXC',
  twse: 'TWSE',
  tpex: 'TPEx',
  nasdaq: 'NSDQ',
  nyse: 'NYSE',
  cboe: 'CBOE',
  treasury: 'UST',
  mock: 'DEMO',
};

/** Venue listings cached per runtime for name search. */
const directories = new WeakMap<
  TerminalRuntime,
  Map<Venue, Promise<readonly Instrument[]>>
>();

/**
 * Returns the full listing of a venue (cached; retried after a failure).
 * The engine matches symbols only, so names are matched against this list.
 * @param runtime Runtime whose market client is asked.
 * @param venue Venue to list.
 * @return Instruments of the venue (at most 5,000).
 */
export function venueDirectory(
  runtime: TerminalRuntime,
  venue: Venue
): Promise<readonly Instrument[]> {
  let cache = directories.get(runtime);
  if (!cache) {
    cache = new Map();
    directories.set(runtime, cache);
  }
  let promise = cache.get(venue);
  if (!promise) {
    promise = runtime.clients.market
      .listInstruments({venue, limit: 5000})
      .then(list => list.instruments);
    // Forget a failed load (unless a newer one replaced it) so the next
    // search retries.
    const own = promise;
    promise.catch(() => {
      if (cache.get(venue) === own) cache.delete(venue);
    });
    cache.set(venue, promise);
  }
  return promise;
}

/**
 * Merges symbol matches from the engine with name matches from venue
 * listings, ranked and limited. Ties sort by group order, then symbol.
 * @param symbolHits Engine search results.
 * @param listings Venue listings for name search (used only with a query).
 * @param query Search text.
 * @param group Selected group (`all` for every group).
 * @return At most `MAX_RESULTS` instruments, best first.
 */
export function rankInstruments(
  symbolHits: readonly Instrument[],
  listings: readonly Instrument[],
  query: string,
  group: MarketGroup | 'all'
): Instrument[] {
  const byId = new Map<string, {instrument: Instrument; rank: number}>();
  const consider = (instrument: Instrument, fallback: number | null) => {
    if (group !== 'all' && marketGroupOf(instrument) !== group) return;
    const rank = searchRank(instrument, query) ?? fallback;
    if (rank === null) return;
    const known = byId.get(instrument.instrument_id);
    if (!known || rank < known.rank) {
      byId.set(instrument.instrument_id, {instrument, rank});
    }
  };
  // The engine matched these on its own rules; keep them even when the
  // browser's ranking does not recognize the match.
  for (const instrument of symbolHits) {
    consider(instrument, 3);
  }
  if (query.trim()) {
    for (const instrument of listings) {
      consider(instrument, null);
    }
  }
  const order = MARKET_GROUPS.map(g => g.id);
  return [...byId.values()]
    .sort(
      (a, b) =>
        a.rank - b.rank ||
        order.indexOf(marketGroupOf(a.instrument)) -
          order.indexOf(marketGroupOf(b.instrument)) ||
        a.instrument.display_symbol.localeCompare(b.instrument.display_symbol)
    )
    .slice(0, MAX_RESULTS)
    .map(entry => entry.instrument);
}

/**
 * Renders the instrument selector: a trigger showing the current symbol and
 * venue that opens the picker in a popover.
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
 * Renders one result row. Its tooltip explains a delay or a missing API
 * key, else shows the canonical id.
 * @param props Instrument, venue capabilities (null while unknown) and pick
 *     handler.
 * @return Option button.
 */
function InstrumentOption({
  item,
  capabilities,
  onPick,
}: {
  item: Instrument;
  capabilities: VenueCapabilities | null;
  onPick: (id: InstrumentId) => void;
}): ReactElement {
  const venue = item.key.venue;
  const access = venueAccess(capabilities);
  const group = marketGroupOf(item);
  const listing = item.listing_exchange ?? null;
  const showListing =
    listing !== null && listing.toUpperCase() !== VENUE_BADGES[venue];
  const type = item.key.market_type;
  const name = [item.name, item.local_name].filter(Boolean).join(' · ');
  const unit =
    type === 'rate'
      ? 'Yield, %'
      : type === 'perp'
        ? `${item.key.settlement}-settled`
        : item.key.quote;
  return (
    <button
      type="button"
      role="option"
      aria-selected={false}
      className="j-menu__item instrument-option"
      data-testid={`instrument-option-${item.instrument_id}`}
      data-group={group}
      title={
        access.missingCredentials ??
        (access.delay
          ? `${access.delay}: this deployment receives delayed data for ${VENUE_NAMES[venue]}`
          : item.instrument_id)
      }
      onClick={() => void onPick(item.instrument_id)}
    >
      <span className="instrument-option__symbol">{item.display_symbol}</span>
      <span className="instrument-option__badges">
        <span
          className={`j-badge${venue === 'mock' ? ' j-badge--demo' : ''}`}
          title={VENUE_NAMES[venue]}
        >
          {VENUE_BADGES[venue]}
        </span>
        {showListing && (
          <span className="j-badge" title={`Listed on ${listing}`}>
            {listing}
          </span>
        )}
        {access.delay && (
          <span
            className="j-badge j-badge--sodium"
            data-testid="instrument-delayed"
          >
            {access.delay}
          </span>
        )}
        {access.missingCredentials && (
          <span
            className="j-badge j-badge--stale"
            data-testid="instrument-no-credentials"
          >
            No API key
          </span>
        )}
      </span>
      {name && <span className="instrument-option__name">{name}</span>}
      <span className="instrument-option__id">{item.instrument_id}</span>
      <span className="instrument-option__meta">
        {marketTypeLabel(type)} · {unit}
      </span>
    </button>
  );
}

/**
 * Renders the picker body: group tabs, venue filter, search box and
 * results.
 * @param props Initial venue, DEMO mode and pick handler.
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
  const [capabilities, setCapabilities] = useState<
    ReadonlyMap<Venue, VenueCapabilities>
  >(new Map());
  const [venue, setVenue] = useState<Venue | 'all'>(demo ? 'mock' : 'all');
  const [group, setGroup] = useState<MarketGroup | 'all'>('all');
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<readonly Instrument[] | null>(null);
  const [listings, setListings] = useState<readonly Instrument[]>([]);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  useEffect(() => {
    loadVenueCapabilities(runtime)
      .then(map => {
        setCapabilities(map);
        const available = [...map.keys()];
        setVenues(available);
        // The chart's venue may not be enabled on this backend; searching it
        // would only fail, so fall back to every available venue.
        setVenue(current =>
          current === 'all' || available.includes(current) ? current : 'all'
        );
      })
      .catch(() => undefined);
  }, [runtime]);
  // Venues the symbol search asks: the chosen venue, else the venues of
  // the selected group (so an empty query still lists, say, TWSE), else all.
  const searched = useMemo((): ReadonlyArray<Venue | undefined> => {
    if (venue !== 'all') return [venue];
    if (group === 'all') return [undefined];
    const wanted = new Set<string>([...GROUP_VENUES[group], 'mock']);
    const own = venues.filter(v => wanted.has(v));
    return own.length > 0 ? own : [undefined];
  }, [venue, group, venues]);
  // Symbol search on the engine, 150 ms after typing pauses.
  useEffect(() => {
    let alive = true;
    const timer = setTimeout(() => {
      Promise.all(
        searched.map(v =>
          runtime.clients.market.listInstruments({
            venue: v,
            q: query.trim() || undefined,
            limit: 200,
          })
        )
      )
        .then(lists => {
          if (!alive) return;
          setHits(lists.flatMap(list => list.instruments));
          setError(null);
        })
        .catch(
          (e: unknown) =>
            alive &&
            void setError(e instanceof Error ? e.message : 'Search failed')
        );
    }, 150);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [runtime, searched, query]);
  // Name search over the listings of exchange venues, loaded only when a
  // name may be meant (non-Latin text, or few symbol matches).
  const wantsNames =
    query.trim().length >= 2 &&
    ([...query].some(char => char.charCodeAt(0) > 127) ||
      (hits?.length ?? 0) < 8);
  const named = useMemo(
    () =>
      wantsNames
        ? venues.filter(
            v => NAMED_VENUES.has(v) && (venue === 'all' || venue === v)
          )
        : [],
    [venues, venue, wantsNames]
  );
  useEffect(() => {
    let alive = true;
    void Promise.allSettled(named.map(v => venueDirectory(runtime, v))).then(
      results => {
        if (!alive) return;
        setListings(
          results.flatMap(result =>
            result.status === 'fulfilled' ? [...result.value] : []
          )
        );
      }
    );
    return () => {
      alive = false;
    };
  }, [runtime, named]);
  const results = useMemo(
    () =>
      hits === null ? null : rankInstruments(hits, listings, query, group),
    [hits, listings, query, group]
  );
  const counts = useMemo(() => {
    const all = rankInstruments(hits ?? [], listings, query, 'all');
    const byGroup = new Map<MarketGroup, number>();
    for (const item of all) {
      const g = marketGroupOf(item);
      byGroup.set(g, (byGroup.get(g) ?? 0) + 1);
    }
    return byGroup;
  }, [hits, listings, query]);
  const sections = useMemo(() => {
    const list = results ?? [];
    if (group !== 'all' || query.trim()) return [{group: null, items: list}];
    return MARKET_GROUPS.filter(g => g.id !== 'all')
      .map(g => ({
        // `filter` above dropped `all`, which it cannot narrow by type.
        group: g.id as MarketGroup,
        items: list.filter(item => marketGroupOf(item) === g.id),
      }))
      .filter(section => section.items.length > 0);
  }, [results, group, query]);
  return (
    <div className="instrument-search">
      <div
        className="instrument-search__groups"
        role="tablist"
        aria-label="Market groups"
      >
        {MARKET_GROUPS.map(g => (
          <button
            key={g.id}
            type="button"
            role="tab"
            aria-selected={group === g.id}
            className="instrument-search__group"
            data-testid={`market-group-${g.id}`}
            onClick={() => void setGroup(g.id)}
          >
            {g.label}
            {g.id !== 'all' && counts.get(g.id) !== undefined && (
              <span className="instrument-search__count">
                {counts.get(g.id)}
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="instrument-search__row">
        <label className="sr-only" htmlFor="venue-filter">
          Venue
        </label>
        <select
          id="venue-filter"
          className="j-select"
          value={venue}
          // The options are `all` and the venues listed below.
          onChange={e => void setVenue(e.target.value as Venue | 'all')}
        >
          {!demo && <option value="all">All venues</option>}
          {venues.map(v => (
            <option key={v} value={v}>
              {VENUE_NAMES[v]}
              {venueAccess(capabilities.get(v)).delay
                ? ` (${venueAccess(capabilities.get(v)).delay?.toLowerCase()})`
                : ''}
            </option>
          ))}
        </select>
      </div>
      <div className="instrument-search__field">
        <Search size={14} aria-hidden="true" />
        <input
          ref={input}
          className="j-input"
          placeholder="Symbol or name, e.g. BTC, AAPL, 2330, 台積電"
          aria-label="Search instruments"
          value={query}
          onChange={e => void setQuery(e.target.value)}
        />
      </div>
      {demo && (
        <p className="j-field__hint">
          Demo mode lists synthetic instruments on the demo venue: crypto
          perpetuals, a US stock, a US bond ETF, a TWSE stock, a TPEx bond ETF
          and the 10-year Treasury yield. Connect a market engine for real
          venues.
        </p>
      )}
      {error && (
        <p className="j-field__error" role="alert">
          {error}
        </p>
      )}
      <div
        className="instrument-search__results"
        role="listbox"
        aria-label="Instruments"
      >
        {sections.map(section => (
          <div
            key={section.group ?? 'results'}
            className="instrument-search__section"
            role="group"
            aria-label={
              section.group ? MARKET_GROUP_LABELS[section.group] : 'Results'
            }
            data-testid={
              section.group ? `market-section-${section.group}` : undefined
            }
          >
            {section.group && (
              <div className="instrument-search__heading" aria-hidden="true">
                {MARKET_GROUP_LABELS[section.group]}
              </div>
            )}
            {section.items.map(item => (
              <InstrumentOption
                key={item.instrument_id}
                item={item}
                capabilities={capabilities.get(item.key.venue) ?? null}
                onPick={onPick}
              />
            ))}
          </div>
        ))}
        {results && results.length === 0 && (
          <p className="j-muted instrument-search__empty">
            {query.trim()
              ? `No instruments match “${query}”.`
              : 'No instruments in this group on the connected venues.'}
          </p>
        )}
      </div>
    </div>
  );
}
