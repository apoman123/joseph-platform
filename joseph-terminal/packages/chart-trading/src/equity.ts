/**
 * @fileoverview Cash-equity and broker rules for the order ticket.
 *
 * Exchange-listed stocks and ETFs trade through securities brokers
 * (Interactive Brokers for Nasdaq/NYSE, SinoPac Shioaji and Cathay for
 * TWSE) or the engine's paper accounts (USD: crypto, Nasdaq, NYSE; TWD:
 * TWSE). This module holds the pure rules the ticket applies before the
 * execution engine re-checks everything, read from the account's
 * `TradingCapabilities`: routing by `tradable_venues`, time in force
 * (DAY only for exchange sessions), TWSE tick ladders and daily price
 * limits, board lots (張) and intraday odd lots (零股, limit DAY only, when
 * `odd_lot`), IBKR's `outside_rth`, market orders while the exchange is
 * closed (`queue_market_when_closed`), long-only cash accounts, settled
 * cash, broker account status, the wording of LIVE confirmations and the
 * server-side LIVE arming of an account (`live_armed_until_ms`,
 * `LIVE_NOT_ARMED`).
 */
import {
  Decimal,
  EXCHANGE_HOURS,
  VENUE_NAMES,
  instrumentCalendarId,
  isExchangeCalendar,
  marketStatus,
  parseInstrumentId,
  stepDecimals,
  type AccountLiveConsentRequest,
  type AccountSummary,
  type BarCalendar,
  type Broker,
  type Instrument,
  type InstrumentId,
  type MarketType,
  type OrderIntentRequest,
  type OrderType,
  type RiskDecision,
  type TickStep,
  type TimeInForce,
  type TradingVenue,
  type Venue,
} from '@joseph/contracts';
import {groupThousands} from './format';

/** Board lots (whole 張 on TWSE) or odd lots (fewer shares than a lot). */
export type EquityLotMode = 'board' | 'odd';

/** Brokers that execute exchange-listed securities. */
export const BROKERS: readonly Broker[] = ['ibkr', 'sinopac', 'cathay'];

/** Human broker names. */
export const BROKER_NAMES: Readonly<Record<Broker, string>> = {
  ibkr: 'Interactive Brokers',
  sinopac: 'SinoPac Securities (Shioaji)',
  cathay: 'Cathay Securities',
};

/** Exchange venues of listed securities. */
export const EXCHANGE_VENUES: readonly Venue[] = ['twse', 'nasdaq', 'nyse'];

/**
 * Exchanges each broker routes to when the engine does not list
 * `tradable_venues` (engine `BROKER_EXCHANGES`).
 */
export const BROKER_EXCHANGES: Readonly<Record<Broker, readonly Venue[]>> = {
  ibkr: ['nasdaq', 'nyse'],
  sinopac: ['twse'],
  cathay: ['twse'],
};

/** Exchanges whose listings trade in each currency (paper accounts). */
const CURRENCY_EXCHANGES: Readonly<Record<string, readonly Venue[]>> = {
  USD: ['nasdaq', 'nyse'],
  TWD: ['twse'],
};

/** Time in force offered for securities, in display order. */
export const EQUITY_TIME_IN_FORCE: readonly TimeInForce[] = [
  'DAY',
  'GTC',
  'IOC',
  'FOK',
];

/** Plain-language names of time-in-force values. */
export const TIME_IN_FORCE_NAMES: Readonly<Record<TimeInForce, string>> = {
  DAY: 'Day (this session)',
  GTC: 'Good till canceled',
  IOC: 'Immediate or cancel',
  FOK: 'Fill or kill',
  POST_ONLY: 'Post only',
};

/** Instrument fields the equity rules read (all optional while loading). */
export type EquityInstrument = Pick<Instrument, 'instrument_id' | 'tick_size'> &
  Partial<
    Pick<
      Instrument,
      | 'tick_ladder'
      | 'price_limit_pct'
      | 'board_lot'
      | 'calendar'
      | 'listing_exchange'
      | 'name'
      | 'local_name'
      | 'display_symbol'
      | 'min_qty'
      | 'max_qty'
      | 'min_notional'
      | 'status'
    >
  > & {key?: Partial<Pick<Instrument['key'], 'quote' | 'canonical_base'>>};

/**
 * Whether a venue is a securities broker.
 * @param venue Trading venue.
 * @return True for `ibkr`, `sinopac` and `cathay`.
 */
export function isBroker(venue: TradingVenue): venue is Broker {
  return (BROKERS as readonly string[]).includes(venue);
}

/**
 * Market type of an instrument id.
 * @param instrumentId Canonical id.
 * @return Market type, or null for invalid ids.
 */
function marketTypeOf(instrumentId: string): MarketType | null {
  try {
    return parseInstrumentId(instrumentId).marketType;
  } catch {
    return null;
  }
}

/**
 * Whether an instrument is an exchange-listed cash security.
 * @param instrumentId Canonical id.
 * @return True for stocks and ETFs.
 */
export function isCashEquityId(instrumentId: string): boolean {
  const type = marketTypeOf(instrumentId);
  return type === 'stock' || type === 'etf';
}

/**
 * Whether an instrument is a published, non-tradable rate series.
 * @param instrumentId Canonical id.
 * @return True for `rate` instruments (e.g. Treasury yields).
 */
export function isRateInstrumentId(instrumentId: string): boolean {
  return marketTypeOf(instrumentId) === 'rate';
}

/**
 * Notice shown instead of an order ticket for a rate.
 * @param instrumentId Canonical id.
 * @param name Instrument name, when known.
 * @return Text.
 */
export function rateNotice(instrumentId: string, name?: string | null): string {
  let label = name ?? instrumentId;
  if (!name) {
    try {
      label = parseInstrumentId(instrumentId).venueMarketId;
    } catch {
      label = instrumentId;
    }
  }
  return `${label} is a published rate, not a tradable instrument, so there is no order ticket. Trade a bond ETF to take a position on rates.`;
}

// Tick ladder and price limits ---------------------------------------------

/**
 * Ladder rungs sorted by their minimum price.
 * @param ladder Ladder.
 * @return Sorted rungs with valid decimals.
 */
function sortedLadder(ladder: readonly TickStep[] | undefined): TickStep[] {
  return (ladder ?? [])
    .filter(
      step => Decimal.isValid(step.min_price) && Decimal.isValid(step.tick_size)
    )
    .sort((a, b) => Decimal.parse(a.min_price).cmp(Decimal.parse(b.min_price)));
}

/**
 * Tick size that applies at a price (engine `Instrument.tick_for`).
 * @param instrument Instrument with `tick_size` and optional ladder.
 * @param price Price.
 * @return Tick size.
 */
export function tickAt(instrument: EquityInstrument, price: Decimal): Decimal {
  let tick = Decimal.parse(instrument.tick_size);
  for (const step of sortedLadder(instrument.tick_ladder)) {
    if (price.cmp(Decimal.parse(step.min_price)) >= 0)
      tick = Decimal.parse(step.tick_size);
  }
  return tick.isZero() ? Decimal.parse(instrument.tick_size) : tick;
}

/**
 * Whether a price lies on the instrument's tick grid at that price.
 * @param instrument Instrument.
 * @param price Price.
 * @return True when valid.
 */
export function isOnTickLadder(
  instrument: EquityInstrument,
  price: Decimal
): boolean {
  return price.isMultipleOf(tickAt(instrument, price));
}

/**
 * Rounds a price to the nearest valid ladder price.
 * @param instrument Instrument.
 * @param price Price.
 * @return Valid price.
 */
export function roundToTickLadder(
  instrument: EquityInstrument,
  price: Decimal
): Decimal {
  const rounded = price.roundToStep(tickAt(instrument, price));
  // Rounding up across a rung (99.98 → 100.00) may land on a coarser tick.
  return isOnTickLadder(instrument, rounded)
    ? rounded
    : rounded.roundToStep(tickAt(instrument, rounded));
}

/**
 * Smallest ladder price at or above a value.
 * @param instrument Instrument.
 * @param price Value.
 * @return Price.
 */
function ceilToLadder(instrument: EquityInstrument, price: Decimal): Decimal {
  const tick = tickAt(instrument, price);
  const floor = price.floorToStep(tick);
  return floor.eq(price) ? floor : floor.add(tick).normalize();
}

/**
 * Largest ladder price at or below a value.
 * @param instrument Instrument.
 * @param price Value.
 * @return Price.
 */
function floorToLadder(instrument: EquityInstrument, price: Decimal): Decimal {
  return price.floorToStep(tickAt(instrument, price));
}

/**
 * Decimal places to show for a price.
 * @param instrument Instrument.
 * @param price Price (ladder rung), or null for the base tick.
 * @return Digits.
 */
export function priceDecimals(
  instrument: EquityInstrument,
  price: Decimal | null
): number {
  const tick = price ? tickAt(instrument, price) : instrument.tick_size;
  return stepDecimals(tick.toString());
}

/**
 * Formats a price with the ladder precision and currency.
 * @param instrument Instrument.
 * @param price Price.
 * @return Text such as `585 TWD` or `182.40 USD`.
 */
export function formatEquityPrice(
  instrument: EquityInstrument,
  price: Decimal
): string {
  const text = groupThousands(price.toFixed(priceDecimals(instrument, price)));
  const currency = instrument.key?.quote;
  return currency ? `${text} ${currency}` : text;
}

/** Daily price-limit band. */
export interface PriceLimitBand {
  /** Lowest allowed price (limit down, on the ladder). */
  lower: Decimal;
  /** Highest allowed price (limit up, on the ladder). */
  upper: Decimal;
  /** Reference price (previous session close). */
  reference: Decimal;
  /** Limit in percent. */
  pct: Decimal;
}

/**
 * Daily price limits: the reference ± `price_limit_pct`, limit up rounded
 * down and limit down rounded up to the ladder (TWSE).
 * @param instrument Instrument.
 * @param reference Previous session close.
 * @return Band, or null when the instrument has no limit.
 */
export function priceLimitBand(
  instrument: EquityInstrument,
  reference: Decimal | null
): PriceLimitBand | null {
  const pctText = instrument.price_limit_pct;
  if (!reference || reference.isZero() || !pctText) return null;
  if (!Decimal.isValid(pctText)) return null;
  const pct = Decimal.parse(pctText);
  const share = pct.div(Decimal.parse('100'));
  const one = Decimal.parse('1');
  return {
    upper: floorToLadder(instrument, reference.mul(one.add(share))),
    lower: ceilToLadder(instrument, reference.mul(one.sub(share))),
    reference,
    pct,
  };
}

/**
 * Whether a price is inside the band.
 * @param band Band.
 * @param price Price.
 * @return True when allowed.
 */
export function withinPriceLimit(
  band: PriceLimitBand,
  price: Decimal
): boolean {
  return price.cmp(band.lower) >= 0 && price.cmp(band.upper) <= 0;
}

/**
 * Reference price for the next session's limits: the latest final daily
 * close (after the session) or the close before today's bar (in session).
 * @param candles Daily candles, oldest first.
 * @return Reference close, or null without data.
 */
export function referenceClose(
  candles: readonly {close: string; is_final: boolean}[]
): Decimal | null {
  const last = candles.at(-1);
  if (!last) return null;
  const pick = last.is_final ? last : candles.at(-2);
  return pick && Decimal.isValid(pick.close) ? Decimal.parse(pick.close) : null;
}

// Lots -------------------------------------------------------------------

/**
 * Shares per board lot.
 * @param instrument Instrument.
 * @return Lot size, or null when orders are in single shares.
 */
export function boardLotOf(instrument: EquityInstrument): Decimal | null {
  const text = instrument.board_lot;
  if (!text || !Decimal.isValid(text)) return null;
  const lot = Decimal.parse(text);
  return lot.isZero() || lot.eq(Decimal.parse('1')) ? null : lot;
}

/** Parsed equity quantity. */
export interface EquityQuantity {
  /** Shares sent to the engine, or null when invalid. */
  shares: Decimal | null;
  /** Board lots, when ordering whole lots. */
  lots: Decimal | null;
  /** Problem, if any. */
  error: string | null;
}

/**
 * Parses the ticket quantity: whole board lots (張) or odd-lot shares on
 * TWSE, whole shares elsewhere.
 * @param instrument Instrument.
 * @param mode Board or odd lot (ignored without a board lot).
 * @param text User text.
 * @return Shares and lots, or an error.
 */
export function parseEquityQuantity(
  instrument: EquityInstrument,
  mode: EquityLotMode,
  text: string
): EquityQuantity {
  const trimmed = text.trim().replace(/,/g, '');
  if (trimmed === '')
    return {shares: null, lots: null, error: 'Enter a quantity.'};
  if (!Decimal.isValid(trimmed) || Decimal.parse(trimmed).isNegative()) {
    return {
      shares: null,
      lots: null,
      error: 'Quantity must be a positive number.',
    };
  }
  const value = Decimal.parse(trimmed);
  const one = Decimal.parse('1');
  const lot = boardLotOf(instrument);
  if (!value.isMultipleOf(one) || value.isZero()) {
    return {
      shares: null,
      lots: null,
      error:
        lot && mode === 'board'
          ? 'Board-lot orders are whole 張 (lots).'
          : 'Orders are in whole shares.',
    };
  }
  if (lot && mode === 'board') {
    return {shares: value.mul(lot), lots: value, error: null};
  }
  if (lot && value.cmp(lot) >= 0) {
    return {
      shares: null,
      lots: null,
      error: `An odd-lot order is 1 to ${groupThousands(lot.sub(one).toString())} shares; use board lots for ${groupThousands(lot.toString())} shares or more.`,
    };
  }
  return {shares: value, lots: null, error: null};
}

/**
 * Formats a share quantity with lots where they apply.
 * @param instrument Instrument.
 * @param shares Shares.
 * @return Text such as `2 張 (2,000 shares)`, `350 shares (odd lot)` or
 *     `100 shares`.
 */
export function formatShares(
  instrument: EquityInstrument,
  shares: Decimal
): string {
  const lot = boardLotOf(instrument);
  const text = `${groupThousands(shares.normalize().toString())} shares`;
  if (!lot) return text;
  if (shares.isMultipleOf(lot)) {
    return `${groupThousands(shares.div(lot).normalize().toString())} 張 (${text})`;
  }
  return shares.cmp(lot) < 0 ? `${text} (odd lot)` : text;
}

// Accounts and brokers ------------------------------------------------------

/**
 * Human name of an account's venue or broker.
 * @param venue Trading venue.
 * @return Name.
 */
export function tradingVenueName(venue: TradingVenue): string {
  return isBroker(venue) ? BROKER_NAMES[venue] : VENUE_NAMES[venue];
}

/**
 * Human name of an exchange venue.
 * @param venue Exchange venue.
 * @return Short name such as `TWSE`.
 */
function exchangeName(venue: Venue): string {
  return venue === 'twse' ? 'TWSE' : venue === 'nasdaq' ? 'Nasdaq' : 'NYSE';
}

/**
 * Joins exchange names, e.g. `Nasdaq and NYSE`.
 * @param venues Exchange venues.
 * @return Text.
 */
function exchangeList(venues: readonly Venue[]): string {
  return venues.map(exchangeName).join(' and ');
}

/** Connection state shown for an account. */
export type AccountConnection =
  'connected' | 'read-only' | 'verifying' | 'disconnected' | 'unavailable';

/** Display status of a trading account. */
export interface AccountStatusView {
  /** Broker, when the account is a securities broker. */
  broker: Broker | null;
  /** Venue or broker name. */
  venueName: string;
  /** Connection state. */
  connection: AccountConnection;
  /** Connection text such as `Connected` or `Awaiting API SDK`. */
  connectionText: string;
  /** Credentials (held on the server): configured, missing or unknown. */
  credentials: 'configured' | 'missing' | 'unknown';
  /** Credentials text. */
  credentialsText: string;
  /** Cathay is shown as awaiting the broker's API SDK. */
  awaitingSdk: boolean;
  /** Whether the account can take orders now (any mode). */
  canTrade: boolean;
  /** Paper or broker simulation environment. */
  simulation: boolean;
  /** Badge tone. */
  tone: 'live' | 'sodium' | 'stale' | 'muted';
  /** Engine notes. */
  notes: readonly string[];
}

/**
 * Display status of an account from what the execution engine reports.
 * Broker credentials live on the execution server (secret files); the
 * terminal only says whether the server reports them configured.
 * @param account Account summary.
 * @return Status view.
 */
export function accountStatusView(account: AccountSummary): AccountStatusView {
  const broker = isBroker(account.venue) ? account.venue : null;
  const notes = account.capabilities.notes;
  const noteText = notes.join(' ').toLowerCase();
  const available = account.capabilities.status === 'AVAILABLE';
  const awaitingSdk =
    broker === 'cathay'
      ? !available || account.status !== 'CONNECTED'
      : /\bsdk\b/.test(noteText) && !available;
  const mentionsMissing =
    /credential|api key|certificate|secret/.test(noteText) &&
    account.status === 'DISCONNECTED';
  let credentials: AccountStatusView['credentials'] = 'unknown';
  if (mentionsMissing) credentials = 'missing';
  else if (
    account.status === 'CONNECTED' ||
    account.status === 'READ_ONLY' ||
    account.status === 'PENDING_VERIFICATION'
  )
    credentials = 'configured';
  let connection: AccountConnection;
  let connectionText: string;
  if (awaitingSdk) {
    connection = 'unavailable';
    connectionText = 'Awaiting API SDK';
  } else if (account.status === 'CONNECTED' && available) {
    connection = 'connected';
    connectionText = account.stale ? 'Connected (stale)' : 'Connected';
  } else if (account.status === 'CONNECTED' || account.status === 'READ_ONLY') {
    connection = 'read-only';
    connectionText = 'Read only';
  } else if (account.status === 'PENDING_VERIFICATION') {
    connection = 'verifying';
    connectionText = 'Verifying permissions';
  } else if (account.status === 'DISCONNECTED') {
    connection = 'disconnected';
    connectionText = 'Disconnected';
  } else {
    connection = 'unavailable';
    connectionText = 'Trading unavailable';
  }
  const canTrade = connection === 'connected' && account.modes.length > 0;
  return {
    broker,
    venueName: tradingVenueName(account.venue),
    connection,
    connectionText,
    credentials,
    credentialsText:
      credentials === 'configured'
        ? 'Credentials configured on the server'
        : credentials === 'missing'
          ? 'Credentials not configured on the server'
          : 'Credentials not reported',
    awaitingSdk,
    canTrade,
    simulation: account.capabilities.simulation === true,
    tone:
      connection === 'connected'
        ? 'live'
        : connection === 'verifying' || connection === 'read-only'
          ? 'sodium'
          : connection === 'disconnected'
            ? 'stale'
            : 'muted',
    notes,
  };
}

/**
 * Settlement currencies of an account.
 * @param account Account.
 * @return Currencies (the account currency when none are listed).
 */
function currenciesOf(account: AccountSummary): readonly string[] {
  const listed = account.capabilities.currencies ?? [];
  return listed.length > 0 ? listed : [account.currency];
}

/**
 * Exchange venues an account routes securities to: the engine's
 * `tradable_venues`, else the broker's exchanges, else (paper accounts)
 * the exchanges of its currency.
 * @param account Account.
 * @return Exchange venues (empty for crypto venue accounts).
 */
export function accountExchanges(account: AccountSummary): readonly Venue[] {
  const listed = account.capabilities.tradable_venues ?? [];
  if (listed.length > 0) return listed;
  if (isBroker(account.venue)) return BROKER_EXCHANGES[account.venue];
  if (account.venue === 'mock') {
    return currenciesOf(account).flatMap(
      currency => CURRENCY_EXCHANGES[currency] ?? []
    );
  }
  return [];
}

/**
 * Exchanges a security belongs to: its venue for exchange listings, or the
 * exchanges of its session calendar for DEMO securities (`mock:stock:*`).
 * @param venue Instrument venue.
 * @param calendar Instrument calendar.
 * @return Exchange venues (empty when unknown).
 */
function securityExchanges(venue: Venue, calendar: string): readonly Venue[] {
  if (EXCHANGE_VENUES.includes(venue)) return [venue];
  if (calendar === 'XTAI') return ['twse'];
  if (calendar === 'XNYS') return ['nasdaq', 'nyse'];
  return [];
}

/**
 * Why an account cannot trade an instrument (engine `equities.route` plus
 * the `tradable_venues` check).
 * @param account Account.
 * @param instrumentId Instrument id.
 * @param instrument Registry entry (calendar), when loaded.
 * @return Reason, or null when the account trades it.
 */
export function accountInstrumentMismatch(
  account: AccountSummary,
  instrumentId: InstrumentId,
  instrument?: Pick<Instrument, 'calendar'> | null
): string | null {
  let parts;
  try {
    parts = parseInstrumentId(instrumentId);
  } catch {
    return 'The instrument id is not valid.';
  }
  if (parts.marketType === 'rate') {
    return 'Rates are published values and cannot be traded.';
  }
  const equity = parts.marketType === 'stock' || parts.marketType === 'etf';
  const venue = account.venue;
  const served = accountExchanges(account);
  if (equity) {
    const listing = securityExchanges(
      parts.venue,
      instrumentCalendarId(instrumentId, instrument ?? null)
    );
    if (listing.length > 0 && listing.some(v => served.includes(v))) {
      return null;
    }
    if (served.length === 0) {
      return 'Stocks and ETFs trade through a broker or securities paper account.';
    }
    const name = isBroker(venue) ? BROKER_NAMES[venue] : account.label;
    return `${name} ${isBroker(venue) ? 'accounts route' : 'routes'} ${exchangeList(served)} listings only.`;
  }
  if (isBroker(venue)) {
    return `${BROKER_NAMES[venue]} accounts trade exchange-listed securities only.`;
  }
  if (venue === 'mock') {
    return currenciesOf(account).includes('USD')
      ? null
      : `The ${currenciesOf(account).join('/')} paper account trades ${exchangeList(served)} securities only.`;
  }
  return parts.venue === venue
    ? null
    : `This account trades on ${VENUE_NAMES[venue]} only.`;
}

/**
 * Picks the account a ticket should use for an instrument.
 * @param accounts Accounts.
 * @param instrumentId Instrument id.
 * @param current Currently selected account id.
 * @param instrument Registry entry, when loaded.
 * @return Account, or null when none exists.
 */
export function preferredAccount(
  accounts: readonly AccountSummary[],
  instrumentId: InstrumentId,
  current: string | null,
  instrument?: Pick<Instrument, 'calendar'> | null
): AccountSummary | null {
  const selected = accounts.find(a => a.account_id === current) ?? null;
  const fits = (a: AccountSummary) =>
    accountInstrumentMismatch(a, instrumentId, instrument) === null;
  if (selected && fits(selected)) return selected;
  const compatible = accounts.filter(fits);
  return (
    compatible.find(a => accountStatusView(a).canTrade) ??
    compatible[0] ??
    selected ??
    accounts[0] ??
    null
  );
}

/** Time-in-force choices for an account. */
export interface TimeInForceChoice {
  /** Values offered, in order. */
  options: TimeInForce[];
  /** Explanation when the choice is limited. */
  note: string | null;
}

/**
 * Time in force for a security order: DAY, GTC, IOC and FOK as the
 * account's capabilities list them (DAY applies to exchange sessions only,
 * so it is never offered for 24x7 instruments). Intraday odd lots are DAY
 * (TWSE ROD) only.
 * @param account Account, or null.
 * @param lotMode Board or odd lot.
 * @return Choices.
 */
export function equityTimeInForce(
  account: AccountSummary | null,
  lotMode: EquityLotMode = 'board'
): TimeInForceChoice {
  if (!account) return {options: ['DAY'], note: null};
  const listed = new Set(account.capabilities.time_in_force);
  const options = EQUITY_TIME_IN_FORCE.filter(tif => listed.has(tif));
  if (lotMode === 'odd') {
    return listed.has('DAY')
      ? {options: ['DAY'], note: 'Odd-lot orders are DAY (ROD) only.'}
      : {options: [], note: 'This account lists no DAY time in force.'};
  }
  if (options.length === 0) {
    return {
      options: [],
      note: 'This account lists no time in force for stocks and ETFs.',
    };
  }
  return {
    options,
    note: listed.has('DAY')
      ? null
      : 'DAY orders are not offered by this account.',
  };
}

/**
 * Order types the account offers (stop orders only where listed; SinoPac
 * has none).
 * @param account Account, or null.
 * @param offered Types the ticket shows.
 * @return Types the account accepts, in the ticket's order.
 */
export function accountOrderTypes(
  account: AccountSummary | null,
  offered: readonly OrderType[]
): OrderType[] {
  if (!account) return [...offered];
  const listed = new Set(account.capabilities.order_types);
  return offered.filter(type => listed.has(type));
}

/**
 * Whether the account offers the outside-RTH flag.
 * @param account Account.
 * @return `shown` for accounts listing it (and IBKR accounts); `enabled`
 *     when the capabilities set `outside_rth`.
 */
export function outsideRthSupport(account: AccountSummary | null): {
  shown: boolean;
  enabled: boolean;
  reason: string | null;
} {
  const enabled = account?.capabilities.outside_rth === true;
  if (!account || (!enabled && account.venue !== 'ibkr')) {
    return {shown: false, enabled: false, reason: null};
  }
  return {
    shown: true,
    enabled,
    reason: enabled ? null : 'This account does not accept outside-RTH orders.',
  };
}

/**
 * Whether intraday odd-lot orders are available.
 * @param account Account.
 * @param instrument Instrument.
 * @return True when the instrument has a board lot and the account's
 *     capabilities set `odd_lot`.
 */
export function oddLotAvailable(
  account: AccountSummary | null,
  instrument: EquityInstrument
): boolean {
  return (
    boardLotOf(instrument) !== null && account?.capabilities.odd_lot === true
  );
}

/**
 * Shares of an instrument the account holds long.
 * @param account Account.
 * @param instrumentId Instrument id.
 * @return Shares (zero when none).
 */
export function heldShares(
  account: AccountSummary,
  instrumentId: string
): Decimal {
  let held = Decimal.zero();
  for (const position of account.positions) {
    if (
      position.instrument_id === instrumentId &&
      position.side === 'long' &&
      Decimal.isValid(position.quantity_base)
    )
      held = held.add(Decimal.parse(position.quantity_base));
  }
  return held;
}

/**
 * Cash line of a cash account: settled and unsettled cash.
 * @param account Account.
 * @return Text such as `Settled cash 5,000,000.00 TWD · unsettled
 *     −120,000.00 TWD`, or null when the engine reports none.
 */
export function cashSummary(account: AccountSummary): string | null {
  const money = (value: string) =>
    `${groupThousands(Decimal.parse(value).toFixed(2)).replace('-', '−')} ${account.currency}`;
  const settled =
    account.settled_cash && Decimal.isValid(account.settled_cash)
      ? `Settled cash ${money(account.settled_cash)}`
      : null;
  const unsettled =
    account.unsettled_cash &&
    Decimal.isValid(account.unsettled_cash) &&
    !Decimal.parse(account.unsettled_cash).isZero()
      ? `unsettled ${money(account.unsettled_cash)}`
      : null;
  if (!settled && !unsettled) return null;
  return [settled, unsettled].filter(Boolean).join(' · ');
}

/**
 * Adds the outside-RTH flag to a security intent when it applies: only
 * when true and the account accepts it (omitted otherwise, as the schema
 * expects).
 * @param request Intent.
 * @param account Account.
 * @param outsideRth Whether the user asked for it.
 * @return Intent.
 */
export function withOutsideRth(
  request: OrderIntentRequest,
  account: AccountSummary | null,
  outsideRth: boolean
): OrderIntentRequest {
  return outsideRth && account?.capabilities.outside_rth === true
    ? {...request, outside_rth: true}
    : request;
}

// Market hours -------------------------------------------------------------

/**
 * City label of a time zone, e.g. `New York`.
 * @param timeZone IANA zone.
 * @return Label.
 */
export function zoneCity(timeZone: string): string {
  return (timeZone.split('/').at(-1) ?? timeZone).replace(/_/g, ' ');
}

/**
 * Formats an instant in an exchange's time zone.
 * @param ms UTC ms.
 * @param timeZone IANA zone.
 * @return Text such as `Mon 30 Nov 09:30 New York`.
 */
export function formatExchangeTime(ms: number, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(ms));
  const get = (type: string) => parts.find(p => p.type === type)?.value ?? '';
  return `${get('weekday')} ${get('day')} ${get('month')} ${get('hour')}:${get('minute')} ${zoneCity(timeZone)}`;
}

/** Market-hours notice for the ticket. */
export interface MarketHoursNotice {
  /** Tone. */
  tone: 'info' | 'warning';
  /** Text. */
  text: string;
}

/** Order details that change the notice. */
export interface MarketHoursOrder {
  /** Order type. */
  orderType: OrderType;
  /** Time in force. */
  timeInForce: TimeInForce;
  /** Outside-RTH flag. */
  outsideRth: boolean;
  /** The account queues DAY market orders placed while closed. */
  queueMarket?: boolean;
}

/**
 * When the next session of a closed market opens, as text.
 * @param calendar Exchange calendar.
 * @param nowMs Clock.
 * @return ` (Mon 30 Nov 09:30 New York)` or an empty string.
 */
function nextOpenText(calendar: BarCalendar, nowMs: number): string {
  const next = marketStatus(calendar, nowMs).next;
  return next
    ? ` (${formatExchangeTime(next.open_ms, calendar.timeZone)})`
    : '';
}

/**
 * Engine rules for orders while the exchange is closed (engine
 * `market_session`): market orders are rejected unless the account queues
 * them (`queue_market_when_closed`) as DAY orders; IOC and FOK need an
 * open session.
 * @param calendar Instrument calendar.
 * @param nowMs Clock.
 * @param account Account.
 * @param order Order type and time in force.
 * @return Blocking message, or null when the order may be sent.
 */
export function closedMarketProblem(
  calendar: BarCalendar,
  nowMs: number,
  account: AccountSummary | null,
  order: {orderType: OrderType; timeInForce: TimeInForce}
): string | null {
  if (calendar.continuous || calendar.isOpen(nowMs)) return null;
  const when = `the next open${nextOpenText(calendar, nowMs)}`;
  if (order.orderType === 'market') {
    if (account?.capabilities.queue_market_when_closed !== true) {
      return `The market is closed: market orders are rejected until ${when}. Use a limit order.`;
    }
    return order.timeInForce === 'DAY'
      ? null
      : `The market is closed: send the market order as DAY to queue it for ${when}.`;
  }
  if (order.timeInForce === 'IOC' || order.timeInForce === 'FOK') {
    return `The market is closed: ${order.timeInForce} orders need an open session (${when}).`;
  }
  return null;
}

/**
 * Market-hours notice from the instrument's session calendar: closed now
 * and when the next session opens, holidays and early closes.
 * @param calendar Session calendar.
 * @param nowMs Clock.
 * @param order Order details.
 * @return Notice, or null for always-open markets and regular sessions.
 */
export function marketHoursNotice(
  calendar: BarCalendar,
  nowMs: number,
  order: MarketHoursOrder
): MarketHoursNotice | null {
  if (calendar.continuous || !isExchangeCalendar(calendar.id)) return null;
  const hours = EXCHANGE_HOURS[calendar.id];
  const status = marketStatus(calendar, nowMs);
  const zone = calendar.timeZone;
  if (status.open && status.session) {
    if (!status.earlyClose) return null;
    return {
      tone: 'warning',
      text: `Early close today: ${hours.market} closes at ${formatExchangeTime(status.session.close_ms, zone)}.`,
    };
  }
  const opens = status.next
    ? ` The next session opens ${formatExchangeTime(status.next.open_ms, zone)}.`
    : '';
  const why = status.holiday
    ? `${hours.market} is closed today for a holiday.`
    : `${hours.market} is closed now.`;
  let effect: string;
  if (order.outsideRth && order.orderType !== 'market') {
    effect =
      ' Outside RTH is on: the broker may fill this order in extended hours.';
  } else if (order.orderType === 'market') {
    effect = order.queueMarket
      ? ' A DAY market order placed now queues for the next open.'
      : ' Market orders are rejected until the open; use a limit order.';
  } else if (order.timeInForce === 'DAY') {
    effect = ' A DAY order placed now is good for the next session only.';
  } else {
    effect = ' The order waits for the next session.';
  }
  return {tone: 'warning', text: `${why}${opens}${effect}`};
}

// LIVE confirmation --------------------------------------------------------

/** Content of the LIVE confirmation step. */
export interface LiveConfirmation {
  /** Dialog title. */
  title: string;
  /** Warning sentence. */
  warning: string;
  /** Label/value rows. */
  rows: readonly [string, string][];
  /** Confirm button naming the exact action. */
  confirmLabel: string;
}

/** Instrument details used by the LIVE confirmation. */
export type LiveInstrument = EquityInstrument & {
  /** Lot size for non-equity quantities. */
  lot_size?: string;
};

/**
 * Builds the LIVE confirmation for a previewed order.
 * @param request Normalized request from the preview.
 * @param instrument Instrument.
 * @param account Account.
 * @param notional Previewed notional, when known.
 * @return Confirmation content.
 */
export function liveConfirmation(
  request: OrderIntentRequest,
  instrument: LiveInstrument,
  account: AccountSummary,
  notional: string | null
): LiveConfirmation {
  const venue = tradingVenueName(account.venue);
  const symbol =
    instrument.display_symbol ??
    parseInstrumentId(request.instrument_id).venueMarketId;
  const currency = instrument.key?.quote ?? account.currency;
  const qty = Decimal.parse(request.quantity_base);
  const equity = isCashEquityId(request.instrument_id);
  const quantity = equity
    ? formatShares(instrument, qty)
    : `${groupThousands(qty.normalize().toString())} ${instrument.key?.canonical_base ?? ''}`.trim();
  const priceText = (text: string | null | undefined) =>
    text && Decimal.isValid(text)
      ? formatEquityPrice(
          {...instrument, key: {...instrument.key, quote: currency}},
          Decimal.parse(text)
        )
      : null;
  const limit = priceText(request.limit_price);
  const trigger = priceText(request.trigger_price);
  const type = request.order_type.replace('_', '-');
  const rows: [string, string][] = [
    ['Mode', 'LIVE money'],
    ['Broker', venue],
    ['Account', account.label],
    ['Action', `${request.side === 'buy' ? 'Buy' : 'Sell'} ${type}`],
    [
      'Instrument',
      `${symbol}${instrument.name ? ` (${instrument.name})` : ''}`,
    ],
    ['Quantity', quantity],
  ];
  if (trigger) rows.push(['Trigger', trigger]);
  rows.push(['Price', limit ?? 'Market (best available price)']);
  rows.push(['Time in force', request.time_in_force]);
  if (notional && Decimal.isValid(notional))
    rows.push([
      'Estimated value',
      `${groupThousands(Decimal.parse(notional).toFixed(2))} ${currency}`,
    ]);
  return {
    title: 'Send a LIVE order',
    warning: `LIVE money: this order goes to ${venue} for account ${account.label} and may fill at once. The execution engine re-checks every risk limit before it is sent.`,
    rows,
    confirmLabel: `Send LIVE ${request.side} ${quantity} ${symbol}${limit ? ` at ${limit}` : ' at market'}`,
  };
}

// Live arming -----------------------------------------------------------------

/** One arming lifetime offered by the ticket. */
export interface LiveArmDuration {
  /** Lifetime in milliseconds. */
  ms: number;
  /** Label, e.g. `1 hour`. */
  label: string;
}

/**
 * Arming lifetimes the ticket offers. All stay within the execution
 * engine's default maximum (8 hours, `JOSEPH_LIVE_ARM_MAX_MS`); the engine
 * refuses anything longer than its configured maximum.
 */
export const LIVE_ARM_DURATIONS: readonly LiveArmDuration[] = [
  {ms: 15 * 60_000, label: '15 minutes'},
  {ms: 60 * 60_000, label: '1 hour'},
  {ms: 4 * 60 * 60_000, label: '4 hours'},
  {ms: 8 * 60 * 60_000, label: '8 hours'},
];

/** Default arming lifetime (1 hour). */
export const LIVE_ARM_DEFAULT_MS = 60 * 60_000;

/**
 * Label of an arming lifetime.
 * @param durationMs Lifetime.
 * @return Label such as `1 hour`.
 */
export function liveArmDurationLabel(durationMs: number): string {
  const known = LIVE_ARM_DURATIONS.find(d => d.ms === durationMs);
  if (known) return known.label;
  const minutes = Math.round(durationMs / 60_000);
  return minutes % 60 === 0 ? `${minutes / 60} hours` : `${minutes} minutes`;
}

/**
 * Builds the account live-consent request (execution engine
 * `POST /v1/accounts/{account_id}/live-consent`).
 * @param code Step-up code as typed.
 * @param durationMs Requested lifetime.
 * @return Request body.
 */
export function liveArmRequest(
  code: string,
  durationMs: number
): AccountLiveConsentRequest {
  return {
    step_up_code: code.trim(),
    acknowledge_risk: true,
    duration_ms: durationMs,
  };
}

/**
 * When the server-side LIVE arming of an account ends.
 * @param account Account summary.
 * @param nowMs Current time (UTC ms).
 * @return End of the arming, or null when the account is not armed (no
 *     arming, revoked or expired).
 */
export function liveArmedUntil(
  account: AccountSummary | null,
  nowMs: number
): number | null {
  const until = account?.live_armed_until_ms ?? null;
  return until !== null && until > nowMs ? until : null;
}

/**
 * Whether a risk decision blocked an order because LIVE is not armed on
 * the account (`LIVE_NOT_ARMED`).
 * @param risk Risk decision of a preview or submission.
 * @return True when a failing check carries `LIVE_NOT_ARMED`.
 */
export function liveNotArmed(risk: RiskDecision | null | undefined): boolean {
  return (
    risk?.checks.some(
      c =>
        c.code === 'LIVE_NOT_ARMED' &&
        (c.outcome === 'FAIL' || c.outcome === 'UNKNOWN')
    ) ?? false
  );
}

/**
 * Consent text shown before arming LIVE.
 * @param account Account.
 * @return Text.
 */
export function liveArmText(account: AccountSummary): string {
  return `Live orders from this ticket go to ${tradingVenueName(account.venue)} account "${account.label}" with real money. The execution engine arms this account until the time shown after you confirm and still checks every LIVE order (account permissions, risk limits, kill switch). You can disarm at any time.`;
}

/**
 * Whether a step-up code looks valid (6 to 8 digits).
 * @param code Code.
 * @return True when well formed.
 */
export function validStepUpCode(code: string): boolean {
  return /^\d{6,8}$/.test(code.trim());
}
