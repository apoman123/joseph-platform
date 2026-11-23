/**
 * @fileoverview Cash-equity and broker rules for the order ticket.
 *
 * Exchange-listed stocks and ETFs trade through securities brokers
 * (Interactive Brokers for Nasdaq/NYSE, SinoPac Shioaji and Cathay for
 * TWSE). This module holds the pure rules the ticket applies before the
 * execution engine re-checks everything: TWSE tick ladders and daily price
 * limits, board lots (張) versus odd lots (零股), time in force per broker,
 * IBKR's outside-regular-trading-hours flag, market-hours notices from the
 * instrument's session calendar, broker account status and the wording of
 * LIVE confirmations.
 *
 * Forward compatibility: the execution schema does not yet carry `DAY`
 * time in force, an outside-RTH flag, an odd-lot flag or broker credential
 * status. Values the engine may add are read defensively
 * (`capabilityExtensions`, `accountExtensions`) and extra request fields
 * are only sent when the account's capabilities say the engine accepts
 * them (`equityIntentExtensions`), so nothing unknown reaches the API.
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
  type AccountSummary,
  type BarCalendar,
  type Broker,
  type Instrument,
  type InstrumentId,
  type MarketType,
  type OrderIntentRequest,
  type TickStep,
  type TimeInForce,
  type TradingVenue,
} from '@joseph/contracts';
import {groupThousands} from './format';

/** Time in force offered by the ticket (`DAY` is not in the schema yet). */
export type TicketTimeInForce = TimeInForce | 'DAY';

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

/**
 * Time in force each broker supports for cash equities: IBKR DAY and GTC;
 * TWSE brokers ROD (rest of day, shown as DAY) only.
 */
export const BROKER_TIME_IN_FORCE: Readonly<
  Record<Broker, readonly TicketTimeInForce[]>
> = {
  ibkr: ['DAY', 'GTC'],
  sinopac: ['DAY'],
  cathay: ['DAY'],
};

/** Plain-language names of time-in-force values. */
export const TIME_IN_FORCE_NAMES: Readonly<Record<TicketTimeInForce, string>> =
  {
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

/** Optional capability fields the brokers engineer may add. */
export interface CapabilityExtensions {
  /** The engine accepts `outside_rth` on intents (IBKR). */
  outsideRth: boolean;
  /** The engine accepts an explicit odd-lot flag on intents (TWSE). */
  oddLot: boolean;
}

/**
 * Reads optional capability fields defensively (unknown today).
 * @param account Account summary.
 * @return Extensions (false when absent).
 */
export function capabilityExtensions(
  account: AccountSummary | null
): CapabilityExtensions {
  const caps = (account?.capabilities ?? {}) as unknown as Record<
    string,
    unknown
  >;
  return {outsideRth: caps.outside_rth === true, oddLot: caps.odd_lot === true};
}

/** Optional account fields the brokers engineer may add. */
export interface AccountExtensions {
  /** Whether broker credentials are configured, when reported. */
  credentialsConfigured: boolean | null;
}

/**
 * Reads optional account fields defensively.
 * @param account Account summary.
 * @return Extensions (null when absent).
 */
export function accountExtensions(account: AccountSummary): AccountExtensions {
  const raw = account as unknown as Record<string, unknown>;
  const flag = raw.credentials_configured;
  return {credentialsConfigured: typeof flag === 'boolean' ? flag : null};
}

/**
 * Human name of an account's venue or broker.
 * @param venue Trading venue.
 * @return Name.
 */
export function tradingVenueName(venue: TradingVenue): string {
  return isBroker(venue) ? BROKER_NAMES[venue] : VENUE_NAMES[venue];
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
  /** Credentials: configured, missing or unknown. */
  credentials: 'configured' | 'missing' | 'unknown';
  /** Credentials text. */
  credentialsText: string;
  /** Cathay is shown as awaiting the broker's API SDK. */
  awaitingSdk: boolean;
  /** Whether the account can take orders now (any mode). */
  canTrade: boolean;
  /** Badge tone. */
  tone: 'live' | 'sodium' | 'stale' | 'muted';
  /** Engine notes. */
  notes: readonly string[];
}

/**
 * Display status of an account from what the execution engine reports.
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
  const reported = accountExtensions(account).credentialsConfigured;
  const mentionsMissing =
    /credential|api key|certificate/.test(noteText) &&
    account.status === 'DISCONNECTED';
  let credentials: AccountStatusView['credentials'] = 'unknown';
  if (reported !== null) credentials = reported ? 'configured' : 'missing';
  else if (mentionsMissing) credentials = 'missing';
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
        ? 'Credentials configured'
        : credentials === 'missing'
          ? 'Credentials missing on the execution engine'
          : 'Credentials not reported',
    awaitingSdk,
    canTrade,
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
 * Why an account cannot trade an instrument.
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
  const calendar = instrumentCalendarId(instrumentId, instrument ?? null);
  const venue = account.venue;
  if (venue === 'ibkr') {
    return equity && calendar === 'XNYS'
      ? null
      : 'Interactive Brokers accounts trade Nasdaq and NYSE listings.';
  }
  if (venue === 'sinopac' || venue === 'cathay') {
    return equity && calendar === 'XTAI'
      ? null
      : `${BROKER_NAMES[venue]} accounts trade TWSE listings.`;
  }
  if (equity) {
    return 'Stocks and ETFs trade through a broker account (IBKR, SinoPac or Cathay).';
  }
  if (venue === 'mock') {
    return parts.venue === 'mock'
      ? null
      : 'The demo account trades demo instruments only.';
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
  options: TicketTimeInForce[];
  /** Explanation when DAY is missing. */
  note: string | null;
}

/**
 * Time in force for a cash-equity order: the broker's supported values
 * that the account's capabilities list (compared as strings, because the
 * generated `TimeInForce` lacks `DAY` today).
 * @param account Account, or null.
 * @return Choices.
 */
export function equityTimeInForce(
  account: AccountSummary | null
): TimeInForceChoice {
  if (!account) return {options: ['GTC'], note: null};
  const listed = new Set<string>(account.capabilities.time_in_force);
  const broker = isBroker(account.venue) ? account.venue : null;
  const wanted: readonly TicketTimeInForce[] = broker
    ? BROKER_TIME_IN_FORCE[broker]
    : ['DAY', 'GTC'];
  const options = wanted.filter(tif => listed.has(tif));
  if (options.length > 0) {
    return {
      options,
      note: options.includes('DAY')
        ? null
        : 'DAY orders are not offered by this account yet.',
    };
  }
  const fallback = (['GTC', 'IOC'] as const).filter(tif => listed.has(tif));
  return {
    options: fallback.length > 0 ? [...fallback] : ['GTC'],
    note: 'This execution engine does not list DAY time in force for this account yet, so orders stay open until canceled.',
  };
}

/**
 * Whether the account offers IBKR's outside-RTH flag.
 * @param account Account.
 * @return `shown` for IBKR accounts; `enabled` when the engine accepts it.
 */
export function outsideRthSupport(account: AccountSummary | null): {
  shown: boolean;
  enabled: boolean;
  reason: string | null;
} {
  if (!account || account.venue !== 'ibkr') {
    return {shown: false, enabled: false, reason: null};
  }
  const enabled = capabilityExtensions(account).outsideRth;
  return {
    shown: true,
    enabled,
    reason: enabled
      ? null
      : 'The execution engine does not accept outside-RTH orders for this account yet.',
  };
}

/** Request fields the brokers engineer is adding (sent only when listed). */
export interface EquityIntentExtensions {
  /** IBKR: may fill outside regular trading hours. */
  outside_rth?: boolean;
  /** TWSE: board or odd lot. */
  lot_type?: EquityLotMode;
}

/**
 * Adds forward-compatible fields to an intent, only those the account's
 * engine accepts; everything else stays implied by the quantity (an odd
 * lot is fewer shares than a board lot).
 * @param request Intent.
 * @param account Account.
 * @param options Ticket choices.
 * @return Intent with supported extensions.
 */
export function equityIntentExtensions(
  request: OrderIntentRequest,
  account: AccountSummary | null,
  options: {outsideRth: boolean; lotMode: EquityLotMode; hasBoardLot: boolean}
): OrderIntentRequest {
  const support = capabilityExtensions(account);
  const extra: EquityIntentExtensions = {};
  if (support.outsideRth && account?.venue === 'ibkr' && options.outsideRth)
    extra.outside_rth = true;
  if (support.oddLot && options.hasBoardLot) extra.lot_type = options.lotMode;
  return Object.keys(extra).length > 0
    ? ({...request, ...extra} as OrderIntentRequest)
    : request;
}

/**
 * Maps a ticket time in force to the contract value. `DAY` passes through
 * only when the account's capabilities list it (the engine then accepts
 * it); the cast is the single place the generated type is widened.
 * @param value Ticket value.
 * @return Contract value.
 */
export function toContractTimeInForce(value: TicketTimeInForce): TimeInForce {
  return value as TimeInForce;
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
  orderType: OrderIntentRequest['order_type'];
  /** Time in force. */
  timeInForce: TicketTimeInForce;
  /** IBKR outside-RTH flag. */
  outsideRth: boolean;
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
      ' Outside RTH is on: Interactive Brokers may fill this limit order in extended hours.';
  } else if (order.orderType === 'market') {
    effect = ' A market order placed now waits and executes at the next open.';
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

/** Body of a live consent request (execution engine `LiveConsentBody`). */
export interface LiveConsentRequest {
  /** Step-up (TOTP) code. */
  step_up_code: string;
  /** Explicit risk acknowledgement. */
  acknowledge_risk: boolean;
  /** Consent lifetime. */
  duration_ms: number;
}

/** Consent granted by the engine. */
export interface LiveConsentGrant {
  /** When it expires (UTC ms). */
  expires_at_ms: number;
}

/**
 * Optional execution-client method for account-level live consent with a
 * step-up code (same body as `POST /v1/deployments/{id}/live-consent`).
 */
export interface LiveArmingClient {
  /**
   * Grants scoped, expiring live consent for manual orders on an account.
   * @param accountId Account id.
   * @param request Step-up code, acknowledgement and lifetime.
   * @return Consent with its expiry.
   */
  grantAccountLiveConsent(
    accountId: string,
    request: LiveConsentRequest
  ): Promise<LiveConsentGrant>;
}

/**
 * Whether an execution client offers account-level live consent.
 * @param client Execution client.
 * @return True when `grantAccountLiveConsent` exists.
 */
export function supportsLiveArming(
  client: unknown
): client is LiveArmingClient {
  return (
    typeof (client as Partial<LiveArmingClient> | null)
      ?.grantAccountLiveConsent === 'function'
  );
}

/** How long a browser-side arm lasts. */
export const LIVE_ARM_MS = 15 * 60_000;

/**
 * Consent text shown before arming LIVE.
 * @param account Account.
 * @return Text.
 */
export function liveArmText(account: AccountSummary): string {
  return `Live orders from this ticket go to ${tradingVenueName(account.venue)} account "${account.label}" with real money. The execution engine decides on every order (account permissions, risk limits, kill switch); arming only unlocks Live in this window for 15 minutes.`;
}

/**
 * Whether a step-up code looks valid (6 to 8 digits).
 * @param code Code.
 * @return True when well formed.
 */
export function validStepUpCode(code: string): boolean {
  return /^\d{6,8}$/.test(code.trim());
}
