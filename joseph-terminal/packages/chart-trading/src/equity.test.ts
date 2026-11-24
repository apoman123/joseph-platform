/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Unit tests for cash-equity and broker rules: tick ladders,
 * price limits, board and odd lots, account status and `tradable_venues`
 * routing, time in force, order types, outside RTH, odd lots, closed
 * markets, cash, market-hours notices, LIVE confirmations and live arming.
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
  type RiskDecision,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  BROKER_NAMES,
  accountExchanges,
  accountInstrumentMismatch,
  accountOrderTypes,
  accountStatusView,
  boardLotOf,
  cashSummary,
  closedMarketProblem,
  equityTimeInForce,
  formatEquityPrice,
  formatExchangeTime,
  formatShares,
  heldShares,
  isCashEquityId,
  isOnTickLadder,
  isRateInstrumentId,
  LIVE_ARM_DEFAULT_MS,
  LIVE_ARM_DURATIONS,
  liveArmDurationLabel,
  liveArmRequest,
  liveArmText,
  liveArmedUntil,
  liveConfirmation,
  liveNotArmed,
  marketHoursNotice,
  oddLotAvailable,
  outsideRthSupport,
  parseEquityQuantity,
  preferredAccount,
  priceDecimals,
  priceLimitBand,
  rateNotice,
  referenceClose,
  roundToTickLadder,
  tickAt,
  tradingVenueName,
  validStepUpCode,
  withOutsideRth,
  withinPriceLimit,
  zoneCity,
} from './equity';
import {
  TPEX_BOND_ETF,
  TWSE_STOCK,
  US_STOCK,
  account,
  brokerAccount,
  paperAccount,
  position,
} from './testing/fixtures';

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

describe('TPEx ETF ladder, limits and lots', () => {
  it('uses the ETF ladder (0.01 below 50, 0.05 from 50)', () => {
    expect(tickAt(TPEX_BOND_ETF, d('26.15')).toString()).toBe('0.01');
    expect(tickAt(TPEX_BOND_ETF, d('50')).toString()).toBe('0.05');
    expect(isOnTickLadder(TPEX_BOND_ETF, d('26.33'))).toBe(true);
    expect(isOnTickLadder(TPEX_BOND_ETF, d('57.72'))).toBe(false);
    expect(roundToTickLadder(TPEX_BOND_ETF, d('57.72')).toString()).toBe(
      '57.7'
    );
    expect(formatEquityPrice(TPEX_BOND_ETF, d('26.15'))).toBe('26.15 TWD');
  });

  it('has no daily limit for foreign-constituent funds', () => {
    expect(priceLimitBand(TPEX_BOND_ETF, d('26.15'))).toBeNull();
    const domestic = {...TPEX_BOND_ETF, price_limit_pct: '10'};
    expect(priceLimitBand(domestic, d('26.35'))?.upper.toString()).toBe(
      '28.98'
    );
    expect(priceLimitBand(domestic, d('26.35'))?.lower.toString()).toBe(
      '23.72'
    );
  });

  it('trades board lots of 1,000 units and odd lots', () => {
    expect(boardLotOf(TPEX_BOND_ETF)?.toString()).toBe('1000');
    expect(formatShares(TPEX_BOND_ETF, d('3000'))).toBe('3 張 (3,000 shares)');
    expect(
      parseEquityQuantity(TPEX_BOND_ETF, 'odd', '250').shares?.toString()
    ).toBe('250');
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

describe('account status', () => {
  it('names brokers and venues', () => {
    expect(tradingVenueName('ibkr')).toBe(BROKER_NAMES.ibkr);
    expect(tradingVenueName('binance')).toBe('Binance USDⓈ-M');
  });

  it('derives connection and server-side credential status', () => {
    const ibkr = accountStatusView(brokerAccount('ibkr'));
    expect(ibkr).toMatchObject({
      broker: 'ibkr',
      connection: 'connected',
      credentials: 'configured',
      credentialsText: 'Credentials configured on the server',
      canTrade: true,
      simulation: true,
      tone: 'live',
    });
    const missing = accountStatusView(
      brokerAccount(
        'sinopac',
        {status: 'DISCONNECTED', modes: []},
        {
          status: 'TRADING_UNAVAILABLE',
          notes: ['The Shioaji API key and CA certificate are not configured.'],
        }
      )
    );
    expect(missing).toMatchObject({
      connection: 'disconnected',
      credentials: 'missing',
      credentialsText: 'Credentials not configured on the server',
      canTrade: false,
      tone: 'stale',
    });
    // Cathay reports TRADING_UNAVAILABLE with an SDK note.
    const cathay = accountStatusView(
      brokerAccount(
        'cathay',
        {status: 'TRADING_UNAVAILABLE', modes: []},
        {
          status: 'TRADING_UNAVAILABLE',
          notes: ['Awaiting the Cathay API SDK.'],
        }
      )
    );
    expect(cathay.awaitingSdk).toBe(true);
    expect(cathay.connectionText).toBe('Awaiting API SDK');
    expect(accountStatusView(brokerAccount('cathay')).awaitingSdk).toBe(false);
    expect(
      accountStatusView(
        brokerAccount(
          'ibkr',
          {},
          {status: 'TRADING_UNAVAILABLE', notes: ['SDK missing']}
        )
      ).awaitingSdk
    ).toBe(true);
  });

  it('maps the remaining account states', () => {
    expect(
      accountStatusView(brokerAccount('ibkr', {status: 'PENDING_VERIFICATION'}))
    ).toMatchObject({connection: 'verifying', credentials: 'configured'});
    expect(
      accountStatusView(brokerAccount('ibkr', {status: 'READ_ONLY'})).connection
    ).toBe('read-only');
    expect(
      accountStatusView(brokerAccount('ibkr', {stale: true})).connectionText
    ).toBe('Connected (stale)');
    expect(
      accountStatusView(account({status: 'TRADING_UNAVAILABLE'}))
    ).toMatchObject({
      connection: 'unavailable',
      credentials: 'unknown',
      credentialsText: 'Credentials not reported',
      simulation: false,
    });
  });
});

describe('routing by tradable venues', () => {
  it('reads tradable_venues, else broker or currency exchanges', () => {
    expect(accountExchanges(brokerAccount('ibkr'))).toEqual([
      'nasdaq',
      'nyse',
      'cboe',
    ]);
    expect(
      accountExchanges(brokerAccount('ibkr', {}, {tradable_venues: undefined}))
    ).toEqual(['nasdaq', 'nyse', 'cboe']);
    expect(
      accountExchanges(
        brokerAccount('sinopac', {}, {tradable_venues: undefined})
      )
    ).toEqual(['twse', 'tpex']);
    expect(
      accountExchanges(
        brokerAccount('cathay', {}, {tradable_venues: undefined})
      )
    ).toEqual(['twse', 'tpex']);
    const twdPaper = paperAccount('TWD');
    expect(accountExchanges(twdPaper)).toEqual(['twse', 'tpex']);
    expect(
      accountExchanges({
        ...twdPaper,
        capabilities: {...twdPaper.capabilities, tradable_venues: []},
      })
    ).toEqual(['twse', 'tpex']);
    expect(accountExchanges(account())).toEqual(['nasdaq', 'nyse', 'cboe']);
    expect(accountExchanges(account({venue: 'binance'}))).toEqual([]);
  });

  it('routes securities to accounts listing their exchange', () => {
    const ibkr = brokerAccount('ibkr');
    const sinopac = brokerAccount('sinopac');
    const usd = paperAccount('USD');
    const twd = paperAccount('TWD');
    expect(accountInstrumentMismatch(ibkr, 'nasdaq:stock:AAPL')).toBeNull();
    expect(accountInstrumentMismatch(ibkr, 'cboe:etf:GOVT')).toBeNull();
    expect(accountInstrumentMismatch(usd, 'cboe:etf:GOVT')).toBeNull();
    expect(accountInstrumentMismatch(twd, 'cboe:etf:GOVT')).toBe(
      'Paper TWD routes TWSE and TPEx listings only.'
    );
    expect(accountInstrumentMismatch(sinopac, 'cboe:etf:GOVT')).toBe(
      'SinoPac Securities (Shioaji) accounts route TWSE and TPEx listings only.'
    );
    expect(accountInstrumentMismatch(ibkr, 'mock:stock:ACME')).toBeNull();
    expect(accountInstrumentMismatch(usd, 'mock:stock:ACME')).toBeNull();
    expect(accountInstrumentMismatch(sinopac, 'twse:stock:2330')).toBeNull();
    expect(accountInstrumentMismatch(sinopac, 'tpex:stock:6488')).toBeNull();
    expect(accountInstrumentMismatch(twd, 'tpex:etf:00679B')).toBeNull();
    expect(
      accountInstrumentMismatch(brokerAccount('cathay'), 'tpex:etf:00937B')
    ).toBeNull();
    expect(
      accountInstrumentMismatch(
        brokerAccount('sinopac', {}, {tradable_venues: ['twse']}),
        'tpex:stock:6488'
      )
    ).toBe('SinoPac Securities (Shioaji) accounts route TWSE listings only.');
    expect(accountInstrumentMismatch(ibkr, 'tpex:etf:00679B')).toBe(
      'Interactive Brokers accounts route Nasdaq, NYSE and Cboe BZX listings only.'
    );
    expect(
      accountInstrumentMismatch(twd, 'mock:stock:9901', {calendar: 'XTAI'})
    ).toBeNull();
    expect(
      accountInstrumentMismatch(ibkr, 'mock:stock:9901', {calendar: 'XTAI'})
    ).toBe(
      'Interactive Brokers accounts route Nasdaq, NYSE and Cboe BZX listings only.'
    );
    expect(accountInstrumentMismatch(usd, 'twse:stock:2330')).toBe(
      'Paper USD routes Nasdaq, NYSE and Cboe BZX listings only.'
    );
    expect(
      accountInstrumentMismatch(account({venue: 'binance'}), 'mock:stock:ACME')
    ).toMatch(/broker or securities paper account/);
  });

  it('keeps crypto on its venue and paper USD accounts', () => {
    expect(
      accountInstrumentMismatch(paperAccount('USD'), 'mock:perp:BTCUSD')
    ).toBeNull();
    expect(
      accountInstrumentMismatch(paperAccount('USD'), 'binance:perp:BTCUSDT')
    ).toBeNull();
    expect(
      accountInstrumentMismatch(paperAccount('TWD'), 'mock:perp:BTCUSD')
    ).toBe('The TWD paper account trades TWSE and TPEx securities only.');
    expect(
      accountInstrumentMismatch(brokerAccount('ibkr'), 'mock:perp:BTCUSD')
    ).toMatch(/exchange-listed securities only/);
    expect(
      accountInstrumentMismatch(
        account({venue: 'binance'}),
        'hyperliquid:perp:BTC'
      )
    ).toMatch(/Binance/);
    expect(
      accountInstrumentMismatch(brokerAccount('ibkr'), 'mock:rate:UST10Y')
    ).toMatch(/cannot be traded/);
    expect(accountInstrumentMismatch(brokerAccount('ibkr'), 'bad id')).toMatch(
      /not valid/
    );
  });

  it('prefers the selected or first connected account that fits', () => {
    const usd = paperAccount('USD');
    const twd = paperAccount('TWD');
    const ibkr = brokerAccount('ibkr');
    const down = brokerAccount('sinopac', {
      account_id: 'acct-down',
      status: 'DISCONNECTED',
      modes: [],
    });
    const all = [usd, twd, ibkr, down];
    expect(preferredAccount(all, 'mock:stock:ACME', null)?.account_id).toBe(
      'acct-paper-demo'
    );
    expect(
      preferredAccount(all, 'mock:stock:ACME', 'acct-ibkr')?.account_id
    ).toBe('acct-ibkr');
    expect(
      preferredAccount(all, 'mock:stock:9901', 'acct-paper-demo', {
        calendar: 'XTAI',
      })?.account_id
    ).toBe('acct-twd-paper-demo');
    expect(preferredAccount([down], 'twse:stock:2330', null)?.account_id).toBe(
      'acct-down'
    );
    expect(preferredAccount([], 'mock:perp:BTCUSD', null)).toBeNull();
    expect(preferredAccount([ibkr], 'mock:perp:BTCUSD', null)?.account_id).toBe(
      'acct-ibkr'
    );
  });
});

describe('order capabilities', () => {
  it('offers the time in force the account lists, DAY first', () => {
    expect(equityTimeInForce(brokerAccount('ibkr'))).toEqual({
      options: ['DAY', 'GTC', 'IOC'],
      note: null,
    });
    expect(equityTimeInForce(brokerAccount('sinopac')).options).toEqual([
      'DAY',
      'IOC',
      'FOK',
    ]);
    expect(equityTimeInForce(null).options).toEqual(['DAY']);
    expect(
      equityTimeInForce(brokerAccount('ibkr', {}, {time_in_force: ['GTC']}))
    ).toEqual({
      options: ['GTC'],
      note: 'DAY orders are not offered by this account.',
    });
    expect(
      equityTimeInForce(brokerAccount('cathay', {}, {time_in_force: []}))
    ).toEqual({
      options: [],
      note: 'This account lists no time in force for stocks and ETFs.',
    });
  });

  it('allows only DAY for intraday odd lots', () => {
    expect(equityTimeInForce(brokerAccount('sinopac'), 'odd')).toEqual({
      options: ['DAY'],
      note: 'Odd-lot orders are DAY (ROD) only.',
    });
    expect(
      equityTimeInForce(
        brokerAccount('sinopac', {}, {time_in_force: ['IOC']}),
        'odd'
      ).options
    ).toEqual([]);
    expect(oddLotAvailable(brokerAccount('sinopac'), TWSE_STOCK)).toBe(true);
    expect(
      oddLotAvailable(
        brokerAccount('sinopac', {}, {odd_lot: false}),
        TWSE_STOCK
      )
    ).toBe(false);
    expect(oddLotAvailable(brokerAccount('ibkr'), US_STOCK)).toBe(false);
  });

  it('offers only the order types the account lists', () => {
    const offered = ['market', 'limit', 'stop_market'] as const;
    expect(accountOrderTypes(brokerAccount('sinopac'), offered)).toEqual([
      'market',
      'limit',
    ]);
    expect(accountOrderTypes(brokerAccount('ibkr'), offered)).toEqual([
      ...offered,
    ]);
    expect(accountOrderTypes(null, offered)).toEqual([...offered]);
  });

  it('shows outside RTH where listed and sends it only when accepted', () => {
    expect(outsideRthSupport(brokerAccount('sinopac')).shown).toBe(false);
    expect(outsideRthSupport(null).shown).toBe(false);
    expect(outsideRthSupport(brokerAccount('ibkr'))).toEqual({
      shown: true,
      enabled: true,
      reason: null,
    });
    expect(
      outsideRthSupport(brokerAccount('ibkr', {}, {outside_rth: false}))
    ).toMatchObject({shown: true, enabled: false});
    const request = {account_id: 'a'} as OrderIntentRequest;
    expect(withOutsideRth(request, brokerAccount('ibkr'), true)).toEqual({
      account_id: 'a',
      outside_rth: true,
    });
    expect(withOutsideRth(request, brokerAccount('ibkr'), false)).toBe(request);
    expect(withOutsideRth(request, brokerAccount('sinopac'), true)).toBe(
      request
    );
  });

  it('counts held shares and summarizes settled cash', () => {
    const holding = brokerAccount('sinopac', {
      positions: [
        position({instrument_id: 'mock:stock:9901', quantity_base: '2000'}),
        position({
          instrument_id: 'mock:stock:9901',
          side: 'short',
          quantity_base: '5',
        }),
      ],
      settled_cash: '5000000',
      unsettled_cash: '-120000',
    });
    expect(heldShares(holding, 'mock:stock:9901').toString()).toBe('2000');
    expect(heldShares(holding, 'mock:stock:ACME').toString()).toBe('0');
    expect(cashSummary(holding)).toBe(
      'Settled cash 5,000,000.00 TWD · unsettled −120,000.00 TWD'
    );
    expect(
      cashSummary({...holding, settled_cash: '10', unsettled_cash: '0'})
    ).toBe('Settled cash 10.00 TWD');
    expect(cashSummary(brokerAccount('ibkr'))).toBeNull();
  });
});

describe('closed markets and notices', () => {
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
  const weekend = createBarCalendar('XNYS');
  const saturday = ny('2026-11-28', 12);

  it('rejects closed-market orders like the engine', () => {
    const ibkr = brokerAccount('ibkr');
    const sinopac = brokerAccount('sinopac');
    expect(
      closedMarketProblem(weekend, saturday, ibkr, {
        orderType: 'market',
        timeInForce: 'DAY',
      })
    ).toBeNull();
    expect(
      closedMarketProblem(weekend, saturday, ibkr, {
        orderType: 'market',
        timeInForce: 'GTC',
      })
    ).toBe(
      'The market is closed: send the market order as DAY to queue it for the next open (Mon 30 Nov 09:30 New York).'
    );
    expect(
      closedMarketProblem(weekend, saturday, sinopac, {
        orderType: 'market',
        timeInForce: 'DAY',
      })
    ).toMatch(/^The market is closed: market orders are rejected until/);
    expect(
      closedMarketProblem(weekend, saturday, ibkr, {
        orderType: 'limit',
        timeInForce: 'IOC',
      })
    ).toMatch(/IOC orders need an open session/);
    expect(
      closedMarketProblem(weekend, saturday, ibkr, {
        orderType: 'limit',
        timeInForce: 'DAY',
      })
    ).toBeNull();
    expect(
      closedMarketProblem(weekend, ny('2026-11-30', 10), sinopac, {
        orderType: 'market',
        timeInForce: 'DAY',
      })
    ).toBeNull();
    expect(
      closedMarketProblem(CONTINUOUS_CALENDAR, 0, null, {
        orderType: 'market',
        timeInForce: 'IOC',
      })
    ).toBeNull();
  });

  it('is silent for 24x7 markets and regular sessions', () => {
    expect(marketHoursNotice(CONTINUOUS_CALENDAR, 0, order)).toBeNull();
    expect(marketHoursNotice(listed, ny('2026-11-25', 11), order)).toBeNull();
  });

  it('warns about early closes, holidays and closed markets', () => {
    expect(marketHoursNotice(listed, ny('2026-11-27', 10), order)?.text).toBe(
      'Early close today: NYSE / Nasdaq closes at Fri 27 Nov 13:00 New York.'
    );
    expect(marketHoursNotice(listed, ny('2026-11-26', 11), order)?.text).toBe(
      'NYSE / Nasdaq is closed today for a holiday. The next session opens Fri 27 Nov 09:30 New York. A DAY order placed now is good for the next session only.'
    );
    expect(
      marketHoursNotice(weekend, saturday, {...order, orderType: 'market'})
        ?.text
    ).toMatch(
      /^NYSE \/ Nasdaq is closed now\. The next session opens Mon 30 Nov 09:30 New York\. Market orders are rejected until the open/
    );
    expect(
      marketHoursNotice(weekend, saturday, {
        ...order,
        orderType: 'market',
        queueMarket: true,
      })?.text
    ).toMatch(/A DAY market order placed now queues for the next open\.$/);
    expect(
      marketHoursNotice(weekend, saturday, {...order, outsideRth: true})?.text
    ).toMatch(/Outside RTH is on/);
    expect(
      marketHoursNotice(weekend, saturday, {...order, timeInForce: 'GTC'})?.text
    ).toMatch(/waits for the next session\.$/);
  });

  it('names the listing exchange of a Taiwan security', () => {
    const xtai = createBarCalendar('XTAI');
    // Saturday 2026-11-28 12:00 Taipei.
    const closed = Date.UTC(2026, 10, 28, 4);
    expect(marketHoursNotice(xtai, closed, order)?.text).toMatch(
      /^TWSE \/ TPEx is closed now\. The next session opens Mon 30 Nov 09:00 Taipei\./
    );
    expect(
      marketHoursNotice(xtai, closed, {...order, market: 'TPEx'})?.text
    ).toMatch(/^TPEx is closed now\./);
  });

  it('formats exchange times with the city', () => {
    expect(zoneCity('America/New_York')).toBe('New York');
    expect(
      formatExchangeTime(ny('2026-11-30', 9, 30), 'America/New_York')
    ).toBe('Mon 30 Nov 09:30 New York');
  });
});

describe('LIVE confirmation and arming', () => {
  const request: OrderIntentRequest = {
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
  };

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
      'Mode': 'LIVE money',
      'Broker': 'SinoPac Securities (Shioaji)',
      'Account': 'sinopac account',
      'Action': 'Buy limit',
      'Quantity': '2 張 (2,000 shares)',
      'Price': '585 TWD',
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
        time_in_force: 'GTC',
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

  it('builds the arming request and validates step-up codes', () => {
    expect(liveArmRequest(' 123456 ', LIVE_ARM_DEFAULT_MS)).toEqual({
      step_up_code: '123456',
      acknowledge_risk: true,
      duration_ms: 3_600_000,
    });
    expect(LIVE_ARM_DURATIONS.map(x => x.label)).toEqual([
      '15 minutes',
      '1 hour',
      '4 hours',
      '8 hours',
    ]);
    expect(
      LIVE_ARM_DURATIONS.every(x => x.ms > 0 && x.ms <= 8 * 3_600_000)
    ).toBe(true);
    expect(liveArmDurationLabel(4 * 3_600_000)).toBe('4 hours');
    expect(liveArmDurationLabel(2 * 3_600_000)).toBe('2 hours');
    expect(liveArmDurationLabel(90 * 60_000)).toBe('90 minutes');
    expect(validStepUpCode('123456')).toBe(true);
    expect(validStepUpCode(' 12345678 ')).toBe(true);
    expect(validStepUpCode('12345')).toBe(false);
    expect(validStepUpCode('12a456')).toBe(false);
    expect(liveArmText(brokerAccount('ibkr'))).toMatch(
      /^Live orders from this ticket go to Interactive Brokers account "ibkr account" with real money\. The execution engine arms this account until the time shown/
    );
  });

  it('reads the server arming and LIVE_NOT_ARMED decisions', () => {
    const ibkr = brokerAccount('ibkr');
    expect(liveArmedUntil(null, 0)).toBeNull();
    expect(liveArmedUntil(ibkr, 0)).toBeNull();
    expect(liveArmedUntil({...ibkr, live_armed_until_ms: null}, 0)).toBeNull();
    expect(liveArmedUntil({...ibkr, live_armed_until_ms: 5_000}, 4_999)).toBe(
      5_000
    );
    expect(
      liveArmedUntil({...ibkr, live_armed_until_ms: 5_000}, 5_000)
    ).toBeNull();
    const decision = (
      outcome: 'FAIL' | 'WARN' | 'UNKNOWN',
      code: 'LIVE_NOT_ARMED' | 'KILL_SWITCH_ENGAGED'
    ): RiskDecision => ({
      decision: outcome === 'WARN' ? 'ALLOW' : 'BLOCK',
      checks: [
        {
          check: 'mode.allowed',
          domain: 'MODE',
          outcome,
          code,
          message: 'm',
          observed: null,
          limit: null,
        },
      ],
      policy_version: 'p',
      evaluated_at_ms: 0,
      reservation_id: null,
    });
    expect(liveNotArmed(decision('FAIL', 'LIVE_NOT_ARMED'))).toBe(true);
    expect(liveNotArmed(decision('UNKNOWN', 'LIVE_NOT_ARMED'))).toBe(true);
    expect(liveNotArmed(decision('WARN', 'LIVE_NOT_ARMED'))).toBe(false);
    expect(liveNotArmed(decision('FAIL', 'KILL_SWITCH_ENGAGED'))).toBe(false);
    expect(liveNotArmed(null)).toBe(false);
    expect(liveNotArmed(undefined)).toBe(false);
  });
});
