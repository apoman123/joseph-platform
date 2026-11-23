/**
 * @fileoverview Unit tests for cash-equity and broker rules: tick ladders,
 * price limits, board and odd lots, broker account status and routing,
 * time in force, outside-RTH, market-hours notices, LIVE confirmations and
 * live arming.
 */
import {
  CONTINUOUS_CALENDAR,
  Decimal,
  EXCHANGE_HOURS,
  SessionCalendar,
  createBarCalendar,
  dayNumberOf,
  zonedTimeToUtc,
  type OrderIntentRequest,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  BROKER_NAMES,
  accountInstrumentMismatch,
  accountStatusView,
  boardLotOf,
  capabilityExtensions,
  equityIntentExtensions,
  equityTimeInForce,
  formatEquityPrice,
  formatExchangeTime,
  formatShares,
  isCashEquityId,
  isOnTickLadder,
  isRateInstrumentId,
  liveArmText,
  liveConfirmation,
  marketHoursNotice,
  outsideRthSupport,
  parseEquityQuantity,
  preferredAccount,
  priceDecimals,
  priceLimitBand,
  rateNotice,
  referenceClose,
  roundToTickLadder,
  supportsLiveArming,
  tickAt,
  toContractTimeInForce,
  tradingVenueName,
  validStepUpCode,
  withinPriceLimit,
  zoneCity,
} from './equity';
import {TWSE_STOCK, US_STOCK, account, brokerAccount} from './testing/fixtures';

/**
 * Parses a decimal.
 * @param text Decimal text.
 * @return Decimal.
 */
function d(text: string): Decimal {
  return Decimal.parse(text);
}

/**
 * New York wall time.
 * @param date `YYYY-MM-DD`.
 * @param hour Hour.
 * @param minute Minute.
 * @return UTC ms.
 */
function ny(date: string, hour = 0, minute = 0): number {
  return zonedTimeToUtc(
    dayNumberOf(date),
    hour * 60 + minute,
    'America/New_York'
  );
}

describe('instrument kinds', () => {
  it('tells stocks, ETFs and rates apart', () => {
    expect(isCashEquityId('mock:stock:ACME')).toBe(true);
    expect(isCashEquityId('nasdaq:etf:TLT')).toBe(true);
    expect(isCashEquityId('binance:perp:BTCUSDT')).toBe(false);
    expect(isCashEquityId('nonsense')).toBe(false);
    expect(isRateInstrumentId('treasury:rate:UST10Y')).toBe(true);
    expect(isRateInstrumentId('mock:stock:ACME')).toBe(false);
    expect(rateNotice('mock:rate:UST10Y', 'US 10-Year Treasury yield')).toMatch(
      /^US 10-Year Treasury yield is a published rate, not a tradable instrument/
    );
    expect(rateNotice('mock:rate:UST10Y')).toMatch(
      /^UST10Y is a published rate/
    );
  });
});

describe('TWSE tick ladder and price limits', () => {
  it('picks the rung at or below the price', () => {
    expect(tickAt(TWSE_STOCK, d('9.99')).toString()).toBe('0.01');
    expect(tickAt(TWSE_STOCK, d('10')).toString()).toBe('0.05');
    expect(tickAt(TWSE_STOCK, d('499.5')).toString()).toBe('0.5');
    expect(tickAt(TWSE_STOCK, d('500')).toString()).toBe('1');
    expect(tickAt(TWSE_STOCK, d('1005')).toString()).toBe('5');
    expect(tickAt(US_STOCK, d('182.4')).toString()).toBe('0.01');
  });

  it('validates and rounds prices to the ladder', () => {
    expect(isOnTickLadder(TWSE_STOCK, d('585'))).toBe(true);
    expect(isOnTickLadder(TWSE_STOCK, d('585.5'))).toBe(false);
    expect(roundToTickLadder(TWSE_STOCK, d('585.4')).toString()).toBe('585');
    expect(roundToTickLadder(TWSE_STOCK, d('1002.6')).toString()).toBe('1005');
    // Rounding up across a rung lands on the coarser grid.
    expect(roundToTickLadder(TWSE_STOCK, d('499.8')).toString()).toBe('500');
    expect(priceDecimals(TWSE_STOCK, d('585'))).toBe(0);
    expect(priceDecimals(TWSE_STOCK, d('45.5'))).toBe(2);
    expect(priceDecimals(US_STOCK, null)).toBe(2);
    expect(formatEquityPrice(TWSE_STOCK, d('1085'))).toBe('1,085 TWD');
    expect(formatEquityPrice(US_STOCK, d('182.4'))).toBe('182.40 USD');
  });

  it('builds the ±10% band on the ladder from the previous close', () => {
    const band = priceLimitBand(TWSE_STOCK, d('585'));
    expect(band?.upper.toString()).toBe('643');
    expect(band?.lower.toString()).toBe('527');
    expect(band && withinPriceLimit(band, d('643'))).toBe(true);
    expect(band && withinPriceLimit(band, d('644'))).toBe(false);
    expect(band && withinPriceLimit(band, d('526'))).toBe(false);
    expect(priceLimitBand(US_STOCK, d('180'))).toBeNull();
    expect(priceLimitBand(TWSE_STOCK, null)).toBeNull();
    expect(
      priceLimitBand({...TWSE_STOCK, price_limit_pct: 'x'}, d('585'))
    ).toBeNull();
  });

  it('takes the reference from the last final daily close', () => {
    const candles = [
      {close: '580', is_final: true},
      {close: '585', is_final: true},
      {close: '590', is_final: false},
    ];
    expect(referenceClose(candles)?.toString()).toBe('585');
    expect(referenceClose(candles.slice(0, 2))?.toString()).toBe('585');
    expect(referenceClose([])).toBeNull();
  });
});

describe('board and odd lots', () => {
  it('reads board lots and converts 張 to shares', () => {
    expect(boardLotOf(TWSE_STOCK)?.toString()).toBe('1000');
    expect(boardLotOf(US_STOCK)).toBeNull();
    expect(boardLotOf({...US_STOCK, board_lot: '1'})).toBeNull();
    const two = parseEquityQuantity(TWSE_STOCK, 'board', '2');
    expect(two.shares?.toString()).toBe('2000');
    expect(two.lots?.toString()).toBe('2');
    expect(parseEquityQuantity(TWSE_STOCK, 'board', '1.5').error).toMatch(
      /whole 張/
    );
  });

  it('limits odd lots to fewer shares than a board lot', () => {
    expect(
      parseEquityQuantity(TWSE_STOCK, 'odd', '350').shares?.toString()
    ).toBe('350');
    expect(parseEquityQuantity(TWSE_STOCK, 'odd', '1000').error).toMatch(
      /odd-lot order is 1 to 999 shares/
    );
    expect(
      parseEquityQuantity(US_STOCK, 'odd', '1,500').shares?.toString()
    ).toBe('1500');
    expect(parseEquityQuantity(US_STOCK, 'odd', '0.5').error).toMatch(
      /whole shares/
    );
    expect(parseEquityQuantity(US_STOCK, 'odd', '').error).toBe(
      'Enter a quantity.'
    );
    expect(parseEquityQuantity(US_STOCK, 'odd', '-3').error).toMatch(
      /positive/
    );
  });

  it('formats shares with lots where they apply', () => {
    expect(formatShares(TWSE_STOCK, d('2000'))).toBe('2 張 (2,000 shares)');
    expect(formatShares(TWSE_STOCK, d('350'))).toBe('350 shares (odd lot)');
    expect(formatShares(TWSE_STOCK, d('1500'))).toBe('1,500 shares');
    expect(formatShares(US_STOCK, d('100'))).toBe('100 shares');
  });
});

describe('broker accounts', () => {
  it('names brokers and venues', () => {
    expect(tradingVenueName('ibkr')).toBe(BROKER_NAMES.ibkr);
    expect(tradingVenueName('binance')).toBe('Binance USDⓈ-M');
  });

  it('derives connection and credential status', () => {
    const ibkr = accountStatusView(brokerAccount('ibkr'));
    expect(ibkr).toMatchObject({
      broker: 'ibkr',
      connection: 'connected',
      credentials: 'configured',
      canTrade: true,
      tone: 'live',
    });
    const missing = accountStatusView(
      brokerAccount('sinopac', {
        status: 'DISCONNECTED',
        modes: [],
        capabilities: {
          ...brokerAccount('sinopac').capabilities,
          status: 'TRADING_UNAVAILABLE',
          notes: ['API key and certificate are not configured.'],
        },
      })
    );
    expect(missing).toMatchObject({
      connection: 'disconnected',
      credentials: 'missing',
      canTrade: false,
      tone: 'stale',
    });
    const cathay = accountStatusView(
      brokerAccount('cathay', {status: 'TRADING_UNAVAILABLE', modes: []})
    );
    expect(cathay.awaitingSdk).toBe(true);
    expect(cathay.connectionText).toBe('Awaiting API SDK');
    // Once the engine reports Cathay connected and available, it is not.
    expect(accountStatusView(brokerAccount('cathay')).awaitingSdk).toBe(false);
  });

  it('reads forward-compatible credential and capability fields', () => {
    const reported = {
      ...brokerAccount('ibkr'),
      credentials_configured: false,
    } as ReturnType<typeof brokerAccount>;
    expect(accountStatusView(reported).credentials).toBe('missing');
    expect(
      accountStatusView(brokerAccount('ibkr', {status: 'PENDING_VERIFICATION'}))
    ).toMatchObject({connection: 'verifying', credentials: 'configured'});
    expect(
      accountStatusView(brokerAccount('ibkr', {status: 'READ_ONLY'})).connection
    ).toBe('read-only');
    expect(
      accountStatusView(account({status: 'TRADING_UNAVAILABLE'}))
    ).toMatchObject({connection: 'unavailable', credentials: 'unknown'});
    expect(
      capabilityExtensions(brokerAccount('ibkr', {}, {outside_rth: true}))
    ).toEqual({outsideRth: true, oddLot: false});
    expect(capabilityExtensions(null)).toEqual({
      outsideRth: false,
      oddLot: false,
    });
  });

  it('routes instruments to the accounts that trade them', () => {
    const ibkr = brokerAccount('ibkr');
    const sinopac = brokerAccount('sinopac');
    const crypto = account();
    expect(accountInstrumentMismatch(ibkr, 'mock:stock:ACME')).toBeNull();
    expect(
      accountInstrumentMismatch(ibkr, 'mock:stock:9901', {calendar: 'XTAI'})
    ).toMatch(/Nasdaq and NYSE/);
    expect(
      accountInstrumentMismatch(sinopac, 'mock:stock:9901', {calendar: 'XTAI'})
    ).toBeNull();
    expect(accountInstrumentMismatch(sinopac, 'twse:stock:2330')).toBeNull();
    expect(accountInstrumentMismatch(crypto, 'mock:stock:ACME')).toMatch(
      /broker account/
    );
    expect(accountInstrumentMismatch(crypto, 'mock:perp:BTCUSD')).toBeNull();
    expect(accountInstrumentMismatch(crypto, 'binance:perp:BTCUSDT')).toMatch(
      /demo instruments/
    );
    expect(
      accountInstrumentMismatch(
        account({venue: 'binance'}),
        'hyperliquid:perp:BTC'
      )
    ).toMatch(/Binance/);
    expect(accountInstrumentMismatch(ibkr, 'mock:rate:UST10Y')).toMatch(
      /cannot be traded/
    );
    expect(accountInstrumentMismatch(ibkr, 'bad id')).toMatch(/not valid/);
  });

  it('prefers a connected account that trades the instrument', () => {
    const crypto = account();
    const ibkr = brokerAccount('ibkr');
    const down = brokerAccount('sinopac', {
      account_id: 'acct-down',
      status: 'DISCONNECTED',
      modes: [],
    });
    const sinopac = brokerAccount('sinopac');
    const all = [crypto, ibkr, down, sinopac];
    expect(preferredAccount(all, 'mock:stock:ACME', null)?.account_id).toBe(
      'acct-ibkr'
    );
    expect(
      preferredAccount(all, 'mock:stock:9901', 'acct-ibkr', {calendar: 'XTAI'})
        ?.account_id
    ).toBe('acct-sinopac');
    expect(
      preferredAccount(all, 'mock:perp:BTCUSD', 'acct-paper-demo')?.account_id
    ).toBe('acct-paper-demo');
    expect(preferredAccount([], 'mock:perp:BTCUSD', null)).toBeNull();
    expect(
      preferredAccount([crypto], 'mock:stock:ACME', null)?.account_id
    ).toBe('acct-paper-demo');
  });
});

describe('time in force and outside RTH', () => {
  it('offers DAY and GTC on IBKR and DAY on TWSE brokers when listed', () => {
    expect(equityTimeInForce(brokerAccount('ibkr'))).toEqual({
      options: ['DAY', 'GTC'],
      note: null,
    });
    expect(equityTimeInForce(brokerAccount('sinopac')).options).toEqual([
      'DAY',
    ]);
    expect(equityTimeInForce(null).options).toEqual(['GTC']);
  });

  it('explains when the engine does not list DAY yet', () => {
    const noDay = brokerAccount('ibkr', {
      capabilities: {
        ...brokerAccount('ibkr').capabilities,
        time_in_force: ['GTC', 'IOC'],
      },
    });
    expect(equityTimeInForce(noDay)).toEqual({
      options: ['GTC'],
      note: 'DAY orders are not offered by this account yet.',
    });
    const none = brokerAccount('sinopac', {
      capabilities: {
        ...brokerAccount('sinopac').capabilities,
        time_in_force: ['IOC'],
      },
    });
    expect(equityTimeInForce(none).options).toEqual(['IOC']);
    expect(equityTimeInForce(none).note).toMatch(/does not list DAY/);
    expect(toContractTimeInForce('DAY')).toBe('DAY');
  });

  it('shows outside RTH for IBKR only and enables it when accepted', () => {
    expect(outsideRthSupport(brokerAccount('sinopac')).shown).toBe(false);
    expect(outsideRthSupport(null).shown).toBe(false);
    expect(outsideRthSupport(brokerAccount('ibkr'))).toMatchObject({
      shown: true,
      enabled: false,
    });
    expect(
      outsideRthSupport(brokerAccount('ibkr', {}, {outside_rth: true}))
    ).toEqual({shown: true, enabled: true, reason: null});
  });

  it('adds intent extensions only when the engine accepts them', () => {
    const request = {account_id: 'a'} as OrderIntentRequest;
    const ibkr = brokerAccount('ibkr', {}, {outside_rth: true});
    expect(
      equityIntentExtensions(request, ibkr, {
        outsideRth: true,
        lotMode: 'board',
        hasBoardLot: false,
      })
    ).toEqual({account_id: 'a', outside_rth: true});
    expect(
      equityIntentExtensions(request, brokerAccount('ibkr'), {
        outsideRth: true,
        lotMode: 'board',
        hasBoardLot: false,
      })
    ).toBe(request);
    expect(
      equityIntentExtensions(
        request,
        brokerAccount('sinopac', {}, {odd_lot: true}),
        {outsideRth: false, lotMode: 'odd', hasBoardLot: true}
      )
    ).toEqual({account_id: 'a', lot_type: 'odd'});
  });
});

describe('market-hours notices', () => {
  const order = {
    orderType: 'limit' as const,
    timeInForce: 'DAY' as const,
    outsideRth: false,
  };
  const listed = new SessionCalendar(EXCHANGE_HOURS.XNYS, {
    sessions: [
      {
        day: '2026-11-25',
        open_ms: ny('2026-11-25', 9, 30),
        close_ms: ny('2026-11-25', 16),
      },
      {
        day: '2026-11-27',
        open_ms: ny('2026-11-27', 9, 30),
        close_ms: ny('2026-11-27', 13),
      },
    ],
    coverage: [{fromMs: ny('2026-11-25'), toMs: ny('2026-11-28')}],
  });

  it('is silent for 24x7 markets and regular sessions', () => {
    expect(marketHoursNotice(CONTINUOUS_CALENDAR, 0, order)).toBeNull();
    expect(marketHoursNotice(listed, ny('2026-11-25', 11), order)).toBeNull();
  });

  it('warns about early closes, holidays and closed markets', () => {
    expect(marketHoursNotice(listed, ny('2026-11-27', 10), order)?.text).toBe(
      'Early close today: NYSE / Nasdaq closes at Fri 27 Nov 13:00 New York.'
    );
    const holiday = marketHoursNotice(listed, ny('2026-11-26', 11), order);
    expect(holiday?.text).toBe(
      'NYSE / Nasdaq is closed today for a holiday. The next session opens Fri 27 Nov 09:30 New York. A DAY order placed now is good for the next session only.'
    );
    const weekend = createBarCalendar('XNYS');
    expect(
      marketHoursNotice(weekend, ny('2026-11-28', 12), {
        ...order,
        orderType: 'market',
      })?.text
    ).toMatch(
      /^NYSE \/ Nasdaq is closed now\. The next session opens Mon 30 Nov 09:30 New York\. A market order placed now waits/
    );
    expect(
      marketHoursNotice(weekend, ny('2026-11-28', 12), {
        ...order,
        outsideRth: true,
      })?.text
    ).toMatch(/Outside RTH is on/);
    expect(
      marketHoursNotice(weekend, ny('2026-11-28', 12), {
        ...order,
        timeInForce: 'GTC',
      })?.text
    ).toMatch(/waits for the next session\.$/);
  });

  it('formats exchange times with the city', () => {
    expect(zoneCity('America/New_York')).toBe('New York');
    expect(
      formatExchangeTime(ny('2026-11-30', 9, 30), 'America/New_York')
    ).toBe('Mon 30 Nov 09:30 New York');
  });
});

describe('LIVE confirmation and arming', () => {
  const request = {
    account_id: 'acct-sinopac',
    instrument_id: 'mock:stock:9901',
    side: 'buy',
    position_effect: 'open',
    order_type: 'limit',
    quantity_base: '2000',
    limit_price: '585',
    trigger_price: null,
    time_in_force: 'DAY',
    reduce_only: false,
    mode: 'LIVE',
    source: 'MANUAL_TICKET',
  } as unknown as OrderIntentRequest;

  it('names LIVE money, broker, account, quantity, price and currency', () => {
    const content = liveConfirmation(
      request,
      TWSE_STOCK,
      brokerAccount('sinopac'),
      '1170000'
    );
    expect(content.title).toBe('Send a LIVE order');
    expect(content.warning).toMatch(/^LIVE money: this order goes to SinoPac/);
    expect(Object.fromEntries(content.rows)).toMatchObject({
      Mode: 'LIVE money',
      Broker: 'SinoPac Securities (Shioaji)',
      Account: 'sinopac account',
      Action: 'Buy limit',
      Quantity: '2 張 (2,000 shares)',
      Price: '585 TWD',
      'Time in force': 'DAY',
      'Estimated value': '1,170,000.00 TWD',
    });
    expect(content.confirmLabel).toBe(
      'Send LIVE buy 2 張 (2,000 shares) 9901 at 585 TWD'
    );
  });

  it('describes market and crypto LIVE orders too', () => {
    const market = liveConfirmation(
      {
        ...request,
        order_type: 'stop_market',
        limit_price: null,
        trigger_price: '600',
      },
      TWSE_STOCK,
      brokerAccount('sinopac'),
      null
    );
    expect(Object.fromEntries(market.rows)).toMatchObject({
      Trigger: '600 TWD',
      Price: 'Market (best available price)',
    });
    expect(market.confirmLabel).toMatch(/at market$/);
    const crypto = liveConfirmation(
      {
        ...request,
        instrument_id: 'mock:perp:BTCUSD',
        quantity_base: '0.010',
        limit_price: '95000',
      },
      {
        instrument_id: 'mock:perp:BTCUSD',
        tick_size: '0.1',
        display_symbol: 'BTCUSD',
        key: {canonical_base: 'BTC', quote: 'USD'},
      },
      account(),
      null
    );
    expect(crypto.confirmLabel).toBe(
      'Send LIVE buy 0.01 BTC BTCUSD at 95,000.0 USD'
    );
  });

  it('detects account-level live consent and validates step-up codes', () => {
    expect(supportsLiveArming({})).toBe(false);
    expect(supportsLiveArming(null)).toBe(false);
    expect(
      supportsLiveArming({grantAccountLiveConsent: () => Promise.resolve()})
    ).toBe(true);
    expect(validStepUpCode('123456')).toBe(true);
    expect(validStepUpCode(' 12345678 ')).toBe(true);
    expect(validStepUpCode('12345')).toBe(false);
    expect(validStepUpCode('12a456')).toBe(false);
    expect(liveArmText(brokerAccount('ibkr'))).toMatch(
      /^Live orders from this ticket go to Interactive Brokers account "ibkr account" with real money/
    );
  });
});
