/**
 * @fileoverview DEMO session lists match the engine's calendars (NYSE
 * holidays and early closes, Treasury market days observed through the
 * engine's table and by rule afterwards, TWSE/TPEx closures), and DEMO
 * session instruments only have bars inside sessions: ACME on XNYS, the
 * TWSE stock with its ladder, the TPEx bond ETF, the US bond ETF and the
 * 10-year yield.
 */
import {
  SessionCalendar,
  EXCHANGE_HOURS,
  dayNumberOf,
  zonedTimeToUtc,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DemoMarketDataClient, demoSpecMatches} from './demo-market-client';
import {
  DEMO_USBOND_EXTRA_CLOSURES,
  DEMO_USBOND_OBSERVED_THROUGH,
  DEMO_XTAI_CLOSURES,
  bondHolidays,
  demoCalendar,
  demoSessionList,
  demoSessionOn,
  easterSunday,
  nthWeekday,
  nyseEarlyClose,
} from './demo-sessions';
import {DEMO_INSTRUMENT_SPECS, demoTickAt} from './prices';

/**
 * New York wall time.
 * @param date `YYYY-MM-DD`.
 * @param hour Hour.
 * @param minute Minute.
 * @return Epoch ms.
 */
function ny(date: string, hour = 0, minute = 0): number {
  return zonedTimeToUtc(
    dayNumberOf(date),
    hour * 60 + minute,
    'America/New_York'
  );
}

const day = dayNumberOf;

describe('XNYS rules (engine parity)', () => {
  it.each([
    '2026-01-01',
    '2026-01-19',
    '2026-02-16',
    '2026-04-03',
    '2026-05-25',
    '2026-06-19',
    '2026-07-03',
    '2026-09-07',
    '2026-11-26',
    '2026-12-25',
    '2027-03-26',
    '2027-06-18',
    '2027-07-05',
    '2027-12-24',
    '2025-01-09',
    '2012-10-29',
  ])('has no session on %s', date => {
    expect(demoSessionOn('XNYS', day(date))).toBeNull();
  });

  it('trades Friday 2021-12-31 (a Saturday New Year is not observed)', () => {
    expect(demoSessionOn('XNYS', day('2021-12-31'))).not.toBeNull();
  });

  it.each([
    ['2026-11-27', 13],
    ['2026-12-24', 13],
    ['2025-07-03', 13],
    ['2026-11-25', 16],
  ])('closes %s at %i:00', (date, hour) => {
    expect(demoSessionOn('XNYS', day(date))?.close_ms).toBe(ny(date, hour));
  });

  it('computes Easter and n-th weekdays', () => {
    expect(easterSunday(2026)).toBe(day('2026-04-05'));
    expect(easterSunday(2027)).toBe(day('2027-03-28'));
    expect(nthWeekday(2026, 11, 3, 4)).toBe(day('2026-11-26'));
    expect(nthWeekday(2026, 5, 0, -1)).toBe(day('2026-05-25'));
    expect(nyseEarlyClose(day('2026-07-03'))).toBe(false);
  });
});

describe('USBOND and XTAI rules', () => {
  it('closes Treasuries on Columbus and Veterans Day, not Good Friday', () => {
    expect(demoSessionOn('USBOND', day('2026-10-12'))).toBeNull();
    expect(demoSessionOn('USBOND', day('2026-11-11'))).toBeNull();
    expect(demoSessionOn('USBOND', day('2026-04-03'))).not.toBeNull();
    expect(demoSessionOn('XNYS', day('2026-10-12'))).not.toBeNull();
    expect(bondHolidays(2026).has(day('2026-04-03'))).toBe(false);
    expect(demoSessionOn('USBOND', day('2026-11-12'))?.open_ms).toBe(
      ny('2026-11-12', 8)
    );
  });

  it('follows the observed Treasury table, then the rule', () => {
    // Past Good Fridays follow Treasury's publications.
    expect(demoSessionOn('USBOND', day('2025-04-18'))).toBeNull();
    expect(demoSessionOn('USBOND', day('2024-03-29'))).toBeNull();
    expect(demoSessionOn('USBOND', day('2023-04-07'))).not.toBeNull();
    expect(demoSessionOn('USBOND', day('2021-04-02'))).not.toBeNull();
    // Future Good Fridays are sessions (known only from SIFMA's notice).
    expect(demoSessionOn('USBOND', day('2027-03-26'))).not.toBeNull();
    expect(demoSessionOn('USBOND', day('2028-04-14'))).not.toBeNull();
    // MLK Day before 1998 and NYSE special closures Treasury observed.
    expect(demoSessionOn('USBOND', day('1995-01-16'))).toBeNull();
    expect(demoSessionOn('USBOND', day('2018-12-05'))).toBeNull();
    expect(demoSessionOn('USBOND', day('2012-10-29'))).not.toBeNull();
    expect(demoSessionOn('USBOND', day('2012-10-30'))).toBeNull();
    expect(DEMO_USBOND_OBSERVED_THROUGH).toBe('2026-11-20');
    expect(
      DEMO_USBOND_EXTRA_CLOSURES.every(d => d <= DEMO_USBOND_OBSERVED_THROUGH)
    ).toBe(true);
  });

  it('uses the TWSE closures (typhoon day, lunar new year)', () => {
    expect(demoSessionOn('XTAI', day('2024-10-31'))).toBeNull();
    expect(demoSessionOn('XTAI', day('2024-10-30'))).not.toBeNull();
    for (const d of [2, 3, 4, 5, 8, 9, 10])
      expect(
        demoSessionOn('XTAI', day(`2027-02-${String(d).padStart(2, '0')}`))
      ).toBeNull();
    expect(demoSessionOn('XTAI', day('2027-02-01'))).not.toBeNull();
    expect(DEMO_XTAI_CLOSURES).toContain('2026-12-25');
  });

  it('lists sessions overlapping a window like the engine', () => {
    const listed = demoSessionList({
      calendar: 'XNYS',
      fromMs: ny('2026-11-20', 12),
      toMs: ny('2026-11-24'),
    });
    expect(listed.sessions.map(s => s.day)).toEqual([
      '2026-11-20',
      '2026-11-23',
    ]);
    expect(
      demoSessionList({calendar: 'XNYS', fromMs: 5, toMs: 5}).sessions
    ).toEqual([]);
  });

  it('builds the same calendar from the list as from the rules', () => {
    const from = ny('2026-11-01');
    const to = ny('2027-01-31');
    const list = demoSessionList({calendar: 'XNYS', fromMs: from, toMs: to});
    const listed = new SessionCalendar(EXCHANGE_HOURS.XNYS, {
      sessions: list.sessions,
      coverage: [{fromMs: from, toMs: to}],
    });
    const ruled = demoCalendar('XNYS');
    for (const start of [ny('2026-11-25', 15, 30), ny('2026-12-23', 15, 30)]) {
      expect(listed.shift('1h', start, 3)).toBe(ruled.shift('1h', start, 3));
    }
    expect(demoCalendar('24x7').continuous).toBe(true);
  });
});

describe('DEMO session instruments', () => {
  const client = new DemoMarketDataClient({now: () => ny('2026-11-23', 12)});

  it('serves ACME bars only inside NYSE sessions', async () => {
    const batch = await client.getCandles({
      instrumentId: 'mock:stock:ACME',
      timeframe: '1h',
      limit: 14,
    });
    const starts = batch.candles.map(c => c.start_ms);
    // Friday 7 bars, then Monday 09:30 and 10:30 and the open 11:30 bar.
    expect(starts.at(-3)).toBe(ny('2026-11-23', 9, 30));
    expect(starts.at(-1)).toBe(ny('2026-11-23', 11, 30));
    expect(starts).toContain(ny('2026-11-20', 15, 30));
    const friday = batch.candles.find(
      c => c.start_ms === ny('2026-11-20', 15, 30)
    );
    expect(friday?.end_ms).toBe(ny('2026-11-20', 16));
    expect(friday?.taker_buy_method).toBe('classified');
    expect(batch.candles.at(-1)?.is_final).toBe(false);
    for (const candle of batch.candles) {
      expect(demoCalendar('XNYS').isOpen(candle.start_ms)).toBe(true);
    }
  });

  it('publishes metadata, classified delta and no volume for the yield', async () => {
    const {instruments} = await client.listInstruments({q: '半導體'});
    expect(instruments.map(i => i.instrument_id)).toEqual(['mock:stock:9901']);
    expect(instruments[0]).toMatchObject({
      calendar: 'XTAI',
      board_lot: '1000',
      price_limit_pct: '10',
      listing_exchange: 'TWSE',
      local_name: '示範半導體',
      key: {quote: 'TWD', market_type: 'stock'},
    });
    const rates = await client.listInstruments({marketType: 'rate'});
    expect(rates.instruments.map(i => i.instrument_id)).toEqual([
      'mock:rate:UST10Y',
    ]);
    const yieldBars = await client.getCandles({
      instrumentId: 'mock:rate:UST10Y',
      timeframe: '1d',
      limit: 3,
    });
    expect(yieldBars.candles[0]?.taker_buy_base_volume).toBeNull();
    expect(yieldBars.candles[0]?.quote_turnover).toBeNull();
    const delta = await client.getIndicatorSeries({
      instrumentId: 'mock:stock:ACME',
      timeframe: '1h',
      pluginId: 'order_flow_pressure',
    });
    expect(delta.points.at(-1)?.values.method).toBe('trade_classification');
    const sessions = await client.getSessions({
      calendar: 'XTAI',
      fromMs: ny('2026-12-21'),
      toMs: ny('2026-12-28'),
    });
    expect(sessions.sessions.map(s => s.day)).not.toContain('2026-12-25');
    const snapshot = await client.getSnapshot('mock:stock:9901');
    expect(snapshot.funding).toBeNull();
    expect(Number(snapshot.last) % 1).toBe(0);
    expect(demoSpecMatches(DEMO_INSTRUMENT_SPECS[0] ?? ({} as never), '')).toBe(
      true
    );
    const tw = DEMO_INSTRUMENT_SPECS.find(
      s => s.instrumentId === 'mock:stock:9901'
    );
    expect(tw && demoTickAt(tw, 45)).toBe('0.05');
  });

  it('serves the TPEx bond ETF on XTAI with the ETF ladder and no limit', async () => {
    const {instruments} = await client.listInstruments({q: '美債'});
    expect(instruments.map(i => i.instrument_id)).toEqual(['mock:etf:00999B']);
    expect(instruments[0]).toMatchObject({
      calendar: 'XTAI',
      asset_class: 'BOND',
      listing_exchange: 'TPEx',
      price_limit_pct: null,
      board_lot: '1000',
      local_name: '示範美債20年',
      key: {quote: 'TWD', market_type: 'etf'},
      tick_ladder: [
        {min_price: '0', tick_size: '0.01'},
        {min_price: '50', tick_size: '0.05'},
      ],
    });
    const batch = await client.getCandles({
      instrumentId: 'mock:etf:00999B',
      timeframe: '1h',
      limit: 10,
    });
    expect(batch.candles.length).toBeGreaterThan(0);
    for (const candle of batch.candles) {
      expect(demoCalendar('XTAI').isOpen(candle.start_ms)).toBe(true);
      expect(Number(candle.close)).toBeLessThan(50);
      expect(Math.round(Number(candle.close) * 100)).toBeCloseTo(
        Number(candle.close) * 100,
        6
      );
    }
    const spec = DEMO_INSTRUMENT_SPECS.find(
      s => s.instrumentId === 'mock:etf:00999B'
    );
    expect(spec && demoTickAt(spec, 26.2)).toBe('0.01');
    expect(spec && demoTickAt(spec, 50.5)).toBe('0.05');
  });

  it('streams the last session bar while the market is closed', async () => {
    const closed = new DemoMarketDataClient({now: () => ny('2026-11-21', 12)});
    const batch = await closed.getCandles({
      instrumentId: 'mock:stock:ACME',
      timeframe: '1h',
      limit: 2,
    });
    expect(batch.candles.map(c => c.start_ms)).toEqual([
      ny('2026-11-20', 14, 30),
      ny('2026-11-20', 15, 30),
    ]);
    expect(batch.candles.every(c => c.is_final)).toBe(true);
    const aois = await closed.getAois({
      instrumentId: 'mock:stock:ACME',
      timeframe: '1h',
    });
    expect(aois.as_of_ms).toBe(ny('2026-11-20', 16));
  });
});
