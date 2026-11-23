/**
 * @fileoverview Session calendars: the engine's scenarios (1h bars at the
 * 09:30 open, early closes, holidays, daylight saving, the TWSE 13:30
 * closing print, periodic bars, shift/floor/barsBetween round trips), the
 * regular-hours fallback, market status and the `24x7` parity with the UTC
 * timeframe math.
 */
import {describe, expect, it} from 'vitest';
import {
  CONTINUOUS_CALENDAR,
  EXCHANGE_HOURS,
  SessionCalendar,
  TIMEFRAMES,
  barsBetween,
  calendarIdForInstrumentId,
  createBarCalendar,
  dayNumberOf,
  dayText,
  floorToTimeframe,
  instrumentCalendarId,
  isPeriodicTimeframe,
  localDayNumber,
  marketStatus,
  shiftTimeframe,
  timeframeEnd,
  weekdayOf,
  zonedTimeToUtc,
  type ExchangeCalendarId,
  type Timeframe,
  type TradingSession,
} from '../src/index';

const NY = 'America/New_York';
const TPE = 'Asia/Taipei';

/**
 * UTC ms of a wall time in a zone.
 * @param zone Time zone.
 * @param date `YYYY-MM-DD`.
 * @param hour Hour.
 * @param minute Minute.
 * @return Epoch ms.
 */
function at(zone: string, date: string, hour = 0, minute = 0): number {
  return zonedTimeToUtc(dayNumberOf(date), hour * 60 + minute, zone);
}

/**
 * New York wall time.
 * @param date `YYYY-MM-DD`.
 * @param hour Hour.
 * @param minute Minute.
 * @return Epoch ms.
 */
const ny = (date: string, hour = 0, minute = 0) => at(NY, date, hour, minute);

/**
 * Taipei wall time.
 * @param date `YYYY-MM-DD`.
 * @param hour Hour.
 * @param minute Minute.
 * @return Epoch ms.
 */
const tpe = (date: string, hour = 0, minute = 0) => at(TPE, date, hour, minute);

/** NYSE closures and early closes in the test windows (engine schedule). */
const NYSE_CLOSED = [
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
  '2027-01-01',
  '2027-01-18',
  '2027-02-15',
  '2027-03-26',
];
const NYSE_EARLY: Readonly<Record<string, number>> = {
  '2026-11-27': 13 * 60,
  '2026-12-24': 13 * 60,
};
/** TWSE closures in the test window. */
const TWSE_CLOSED = ['2026-10-09', '2026-10-23', '2026-10-26', '2026-12-25'];
/** Treasury closures in the test window (Columbus, Veterans Day, ...). */
const BOND_CLOSED = [
  '2026-01-01',
  '2026-01-19',
  '2026-02-16',
  '2026-05-25',
  '2026-06-19',
  '2026-07-03',
  '2026-09-07',
  '2026-10-12',
  '2026-11-11',
  '2026-11-26',
  '2026-12-25',
  '2027-01-01',
];

/**
 * Builds a session list for a range of days.
 * @param id Calendar.
 * @param from First date.
 * @param to Last date (inclusive).
 * @param closed Closed weekdays.
 * @param early Early closes (minutes after midnight).
 * @return Calendar covering the range.
 */
function listCalendar(
  id: ExchangeCalendarId,
  from: string,
  to: string,
  closed: readonly string[],
  early: Readonly<Record<string, number>> = {}
): SessionCalendar {
  const hours = EXCHANGE_HOURS[id];
  const sessions: TradingSession[] = [];
  const shut = new Set(closed);
  for (let day = dayNumberOf(from); day <= dayNumberOf(to); day += 1) {
    const text = dayText(day);
    const weekday = weekdayOf(day);
    if (weekday === 0 || weekday === 6 || shut.has(text)) continue;
    sessions.push({
      day: text,
      open_ms: zonedTimeToUtc(day, hours.openMinute, hours.timeZone),
      close_ms: zonedTimeToUtc(
        day,
        early[text] ?? hours.closeMinute,
        hours.timeZone
      ),
    });
  }
  return new SessionCalendar(hours, {
    sessions,
    coverage: [
      {
        fromMs: zonedTimeToUtc(dayNumberOf(from), 0, hours.timeZone),
        toMs: zonedTimeToUtc(dayNumberOf(to) + 1, 0, hours.timeZone),
      },
    ],
  });
}

const XNYS = listCalendar(
  'XNYS',
  '2025-10-01',
  '2027-04-30',
  NYSE_CLOSED,
  NYSE_EARLY
);
const XTAI = listCalendar('XTAI', '2026-09-01', '2027-01-31', TWSE_CLOSED);
const USBOND = listCalendar('USBOND', '2025-12-01', '2027-01-31', BOND_CLOSED);

describe('time zones', () => {
  it('converts wall times across daylight saving', () => {
    // 09:30 is 14:30 UTC in winter (EST) and 13:30 UTC in summer (EDT).
    expect(ny('2026-03-06', 9, 30)).toBe(Date.UTC(2026, 2, 6, 14, 30));
    expect(ny('2026-03-09', 9, 30)).toBe(Date.UTC(2026, 2, 9, 13, 30));
    expect(ny('2026-11-02', 9, 30)).toBe(Date.UTC(2026, 10, 2, 14, 30));
    // Taiwan has no daylight saving: 09:00 is always 01:00 UTC.
    expect(tpe('2026-07-01', 9)).toBe(Date.UTC(2026, 6, 1, 1));
    expect(localDayNumber(Date.UTC(2026, 6, 1, 23), TPE)).toBe(
      dayNumberOf('2026-07-02')
    );
  });
});

describe('XNYS bars (engine scenarios)', () => {
  it('starts 1h bars at the open and cuts the last at the close', () => {
    expect(XNYS.floor('1h', ny('2026-11-23', 10, 15))).toBe(
      ny('2026-11-23', 9, 30)
    );
    expect(XNYS.endOf('1h', ny('2026-11-23', 15, 30))).toBe(
      ny('2026-11-23', 16)
    );
    expect(
      XNYS.barsBetween('1h', ny('2026-11-23', 9, 30), ny('2026-11-23', 16))
    ).toBe(7);
    // The early close after Thanksgiving leaves 4 hourly bars.
    expect(
      XNYS.barsBetween('1h', ny('2026-11-27', 9, 30), ny('2026-11-27', 16))
    ).toBe(4);
    expect(XNYS.endOf('1h', ny('2026-11-27', 12, 30))).toBe(
      ny('2026-11-27', 13)
    );
  });

  it('splits the session into 4h bars', () => {
    expect(XNYS.floor('4h', ny('2026-11-23', 14))).toBe(
      ny('2026-11-23', 13, 30)
    );
    expect(XNYS.endOf('4h', ny('2026-11-23', 13, 30))).toBe(
      ny('2026-11-23', 16)
    );
  });

  it('floors off-session times to the last bar before them', () => {
    const saturday = ny('2026-11-21', 12);
    expect(XNYS.floor('1h', saturday)).toBe(ny('2026-11-20', 15, 30));
    expect(XNYS.floor('1d', saturday)).toBe(ny('2026-11-20', 9, 30));
    expect(XNYS.isOpen(saturday)).toBe(false);
    expect(XNYS.isOpen(ny('2026-11-20', 15, 59))).toBe(true);
    expect(XNYS.isOpen(ny('2026-11-20', 16))).toBe(false);
  });

  it('skips weekends and holidays when shifting', () => {
    const fridayLast = ny('2026-01-16', 15, 30);
    const tuesdayOpen = ny('2026-01-20', 9, 30);
    expect(XNYS.shift('1h', fridayLast, 1)).toBe(tuesdayOpen);
    expect(XNYS.shift('1h', tuesdayOpen, -1)).toBe(fridayLast);
    // A bar end (the close) counts from the next bar start.
    expect(XNYS.shift('1h', ny('2026-01-16', 16), 0)).toBe(tuesdayOpen);
  });

  it('builds daily, weekly and monthly bars from sessions', () => {
    const tuesday = ny('2026-11-24', 11);
    expect(XNYS.floor('1d', tuesday)).toBe(ny('2026-11-24', 9, 30));
    expect(XNYS.endOf('1d', ny('2026-11-24', 9, 30))).toBe(
      ny('2026-11-24', 16)
    );
    const week = XNYS.floor('1w', tuesday);
    expect(week).toBe(ny('2026-11-23', 9, 30));
    expect(XNYS.endOf('1w', week)).toBe(ny('2026-11-27', 13));
    const month = XNYS.floor('1M', tuesday);
    expect(month).toBe(ny('2026-11-02', 9, 30));
    expect(XNYS.endOf('1M', month)).toBe(ny('2026-11-30', 16));
    expect(XNYS.shift('1M', month, 1)).toBe(ny('2026-12-01', 9, 30));
    // January 2027 starts on Monday the 4th (New Year's Day is a Friday).
    expect(XNYS.shift('1M', month, 2)).toBe(ny('2027-01-04', 9, 30));
    expect(XNYS.barsBetween('1d', month, XNYS.endOf('1M', month))).toBe(20);
  });

  it('reports the last closed bar per session', () => {
    const fridayOpen = ny('2026-11-20', 9, 30);
    expect(XNYS.lastClosedStart('1d', ny('2026-11-23', 10))).toBe(fridayOpen);
    expect(XNYS.lastClosedStart('1d', ny('2026-11-20', 16))).toBe(fridayOpen);
    expect(XNYS.lastClosedStart('1d', ny('2026-11-20', 15))).toBe(
      ny('2026-11-19', 9, 30)
    );
  });

  it('nests intraday bars by divisibility and everything in a session', () => {
    expect(XNYS.contains('4h', '1h')).toBe(true);
    expect(XNYS.contains('1d', '4h')).toBe(true);
    expect(XNYS.contains('1d', '12h')).toBe(true);
    expect(XNYS.contains('1w', '1d')).toBe(true);
    expect(XNYS.contains('1M', '1d')).toBe(true);
    expect(XNYS.contains('1M', '1w')).toBe(false);
    expect(XNYS.contains('1h', '4h')).toBe(false);
    expect(CONTINUOUS_CALENDAR.contains('1d', '1w')).toBe(false);
    expect(CONTINUOUS_CALENDAR.contains('1w', '4h')).toBe(true);
  });

  it('lists sessions overlapping a window in order', () => {
    const listed = XNYS.sessions(ny('2026-11-20', 12), ny('2026-11-24'));
    expect(listed.map(s => s.day)).toEqual(['2026-11-20', '2026-11-23']);
    expect(XNYS.sessions(ny('2026-11-24'), ny('2026-11-24'))).toEqual([]);
    expect(XNYS.sessionOnDay('2026-11-26')).toBeNull();
    expect(XNYS.sessionOnDay('2026-11-27')?.close_ms).toBe(
      ny('2026-11-27', 13)
    );
  });

  it('moves the UTC open with daylight saving', () => {
    expect(XNYS.floor('1d', Date.UTC(2026, 2, 6, 18))).toBe(
      Date.UTC(2026, 2, 6, 14, 30)
    );
    expect(XNYS.floor('1d', Date.UTC(2026, 2, 9, 18))).toBe(
      Date.UTC(2026, 2, 9, 13, 30)
    );
    // 1h bars after the switch still start on the half hour local time.
    expect(XNYS.shift('1h', Date.UTC(2026, 2, 6, 20, 30), 1)).toBe(
      Date.UTC(2026, 2, 9, 13, 30)
    );
  });
});

describe('XTAI bars', () => {
  it('starts at 09:00 and puts the 13:30 closing print in the last bar', () => {
    expect(XTAI.floor('1h', tpe('2026-11-23', 13, 10))).toBe(
      tpe('2026-11-23', 13)
    );
    expect(XTAI.endOf('1h', tpe('2026-11-23', 13))).toBe(
      tpe('2026-11-23', 13, 30)
    );
    const auction = tpe('2026-11-23', 13, 30);
    expect(XTAI.floor('1m', auction)).toBe(tpe('2026-11-23', 13, 29));
    expect(XTAI.floor('1m', auction + 30_000)).toBe(tpe('2026-11-23', 13, 29));
    expect(
      XTAI.barsBetween('1h', tpe('2026-11-23', 9), tpe('2026-11-23', 14))
    ).toBe(5);
  });

  it('skips listed TWSE closures', () => {
    expect(XTAI.sessionOnDay('2026-10-09')).toBeNull();
    expect(XTAI.sessionOnDay('2026-10-08')).not.toBeNull();
    // Friday 2026-10-23 and Monday 10-26 are closed: Thursday → Tuesday.
    expect(XTAI.shift('1d', tpe('2026-10-22', 9), 1)).toBe(
      tpe('2026-10-27', 9)
    );
  });
});

describe('USBOND bars', () => {
  it('closes on Columbus and Veterans Day but opens on Good Friday', () => {
    expect(USBOND.sessionOnDay('2026-10-12')).toBeNull();
    expect(USBOND.sessionOnDay('2026-11-11')).toBeNull();
    expect(USBOND.sessionOnDay('2026-04-03')).not.toBeNull();
    expect(XNYS.sessionOnDay('2026-10-12')).not.toBeNull();
    expect(USBOND.floor('1d', ny('2026-11-12', 12))).toBe(ny('2026-11-12', 8));
  });
});

describe('regular-hours fallback', () => {
  it('uses weekdays and regular hours outside the session list', () => {
    const bare = createBarCalendar('XNYS');
    expect(bare.continuous).toBe(false);
    // Thanksgiving is unknown without a list, so it follows the weekday rule.
    expect(bare.isOpen(ny('2026-11-26', 10))).toBe(true);
    expect(bare.floor('1h', ny('2026-11-28', 10))).toBe(
      ny('2026-11-27', 15, 30)
    );
    // Beyond the list's coverage the listed calendar falls back too.
    expect(XNYS.sessionOnDay('2028-01-03')?.open_ms).toBe(
      ny('2028-01-03', 9, 30)
    );
  });

  it('treats covered days without a listed session as closed', () => {
    const empty = new SessionCalendar(EXCHANGE_HOURS.XNYS, {
      sessions: [],
      coverage: [{fromMs: ny('2026-11-23'), toMs: ny('2026-11-25')}],
    });
    expect(empty.covers(dayNumberOf('2026-11-23'))).toBe(true);
    expect(empty.covers(dayNumberOf('2026-11-25'))).toBe(false);
    expect(empty.sessionOnDay('2026-11-24')).toBeNull();
    expect(empty.sessionOnDay('2026-11-25')).not.toBeNull();
  });
});

describe('market status', () => {
  it('is open during a session with the close and next open', () => {
    const status = marketStatus(XNYS, ny('2026-11-24', 11));
    expect(status.open).toBe(true);
    expect(status.session?.close_ms).toBe(ny('2026-11-24', 16));
    expect(status.next?.day).toBe('2026-11-25');
    expect(status.holiday).toBe(false);
  });

  it('reports holidays, weekends and early closes', () => {
    const thanksgiving = marketStatus(XNYS, ny('2026-11-26', 11));
    expect(thanksgiving.open).toBe(false);
    expect(thanksgiving.holiday).toBe(true);
    expect(thanksgiving.next?.day).toBe('2026-11-27');
    const friday = marketStatus(XNYS, ny('2026-11-27', 11));
    expect(friday.earlyClose).toBe(true);
    const saturday = marketStatus(XNYS, ny('2026-11-28', 11));
    expect(saturday.open).toBe(false);
    expect(saturday.holiday).toBe(false);
    expect(saturday.next?.day).toBe('2026-11-30');
    expect(marketStatus(CONTINUOUS_CALENDAR, 0).open).toBe(true);
  });
});

describe('instrument calendars', () => {
  it('maps venues and DEMO stocks like the engine', () => {
    expect(calendarIdForInstrumentId('twse:stock:2330')).toBe('XTAI');
    expect(calendarIdForInstrumentId('nasdaq:etf:TLT')).toBe('XNYS');
    expect(calendarIdForInstrumentId('nyse:stock:IBM')).toBe('XNYS');
    expect(calendarIdForInstrumentId('treasury:rate:UST10Y')).toBe('USBOND');
    expect(calendarIdForInstrumentId('binance:perp:BTCUSDT')).toBe('24x7');
    expect(calendarIdForInstrumentId('mock:stock:ACME')).toBe('XNYS');
    expect(calendarIdForInstrumentId('mock:perp:BTCUSD')).toBe('24x7');
    expect(calendarIdForInstrumentId('not an id')).toBe('24x7');
    expect(instrumentCalendarId('mock:stock:2330', {calendar: 'XTAI'})).toBe(
      'XTAI'
    );
    expect(instrumentCalendarId('mock:stock:2330', null)).toBe('XNYS');
  });
});

/**
 * Deterministic PRNG (mulberry32).
 * @param seed Seed.
 * @return Generator of [0, 1).
 */
function prng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const ROUND_TRIP_TFS: readonly Timeframe[] = [
  '1m',
  '5m',
  '30m',
  '1h',
  '4h',
  '1d',
  '1w',
  '1M',
];

describe('boundary arithmetic round trips (engine property test)', () => {
  const cases: [string, SessionCalendar, number, number][] = [
    ['XNYS', XNYS, ny('2025-12-15'), ny('2027-02-01')],
    ['XTAI', XTAI, tpe('2026-10-01'), tpe('2026-12-20')],
    ['USBOND', USBOND, ny('2026-01-15'), ny('2026-12-15')],
  ];
  for (const [name, calendar, low, high] of cases) {
    it(`agrees on floor, endOf, shift and barsBetween on ${name}`, () => {
      const random = prng(name.length * 7919);
      for (let i = 0; i < 300; i += 1) {
        const tf =
          ROUND_TRIP_TFS[Math.floor(random() * ROUND_TRIP_TFS.length)] ?? '1h';
        const ts = Math.floor(low + random() * (high - low));
        const bars = Math.floor(random() * 81) - 40;
        const start = calendar.floor(tf, ts);
        expect(start).toBeLessThanOrEqual(ts);
        expect(calendar.isAligned(tf, start)).toBe(true);
        const end = calendar.endOf(tf, start);
        expect(end).toBeGreaterThan(start);
        const following = calendar.shift(tf, start, 1);
        expect(following).toBeGreaterThanOrEqual(end);
        expect(calendar.floor(tf, following)).toBe(following);
        const moved = calendar.shift(tf, start, bars);
        expect(calendar.shift(tf, moved, -bars)).toBe(start);
        if (bars >= 0) {
          expect(calendar.barsBetween(tf, start, moved)).toBe(bars);
        } else {
          expect(calendar.barsBetween(tf, moved, start)).toBe(-bars);
        }
      }
    });
  }
});

describe('24x7 keeps the UTC timeframe math', () => {
  it('matches identity.ts on every timeframe', () => {
    const random = prng(42);
    for (let i = 0; i < 400; i += 1) {
      const tf = TIMEFRAMES[i % TIMEFRAMES.length] ?? '1m';
      const ts = Math.floor(Date.UTC(2020, 0, 1) + random() * 2e11);
      const start = floorToTimeframe(tf, ts);
      const calendar = CONTINUOUS_CALENDAR;
      expect(calendar.floor(tf, ts)).toBe(start);
      expect(calendar.endOf(tf, start)).toBe(timeframeEnd(tf, start));
      expect(calendar.shift(tf, start, 5)).toBe(shiftTimeframe(tf, start, 5));
      expect(calendar.barsBetween(tf, start, ts + 9e8)).toBe(
        barsBetween(tf, start, ts + 9e8)
      );
      expect(calendar.isAligned(tf, start)).toBe(true);
      expect(calendar.isOpen(ts)).toBe(true);
    }
    expect(CONTINUOUS_CALENDAR.sessions(0, 1e12)).toEqual([]);
    expect(CONTINUOUS_CALENDAR.lastClosedStart('1h', 3_600_000 * 5)).toBe(
      3_600_000 * 4
    );
    expect(CONTINUOUS_CALENDAR.lastClosedStart('1h', 10)).toBeNull();
    expect(isPeriodicTimeframe('1w')).toBe(true);
    expect(isPeriodicTimeframe('12h')).toBe(false);
  });
});
