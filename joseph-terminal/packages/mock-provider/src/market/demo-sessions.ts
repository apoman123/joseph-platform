/**
 * @fileoverview DEMO session lists for the exchange calendars, standing in
 * for the engine's `GET /v1/calendars/{calendar_id}/sessions`.
 *
 * - XNYS (NYSE/Nasdaq, 09:30-16:00 America/New_York): a port of the engine's
 *   rules — New Year's Day (a Saturday is not moved to Friday), Martin
 *   Luther King Jr. Day, Washington's Birthday, Good Friday, Memorial Day,
 *   Juneteenth (from 2022), Independence Day, Labor Day, Thanksgiving and
 *   Christmas with weekend observance, the ad hoc closures the engine lists,
 *   and 13:00 early closes on July 3, Christmas Eve (Monday-Thursday) and
 *   the day after Thanksgiving.
 * - USBOND (08:00-17:00 America/New_York): the NYSE holidays without Good
 *   Friday, plus Columbus Day and Veterans Day.
 * - XTAI (TWSE, 09:00-13:30 Asia/Taipei): weekdays minus the closures of the
 *   engine's TWSE table for 2024-2027 (observed through 2026-11-22 and
 *   published through 2027); later dates follow the weekday rule.
 */
import {
  CONTINUOUS_CALENDAR,
  EXCHANGE_HOURS,
  SessionCalendar,
  dayNumberOf,
  dayText,
  isExchangeCalendar,
  localDayNumber,
  weekdayOf,
  zonedTimeToUtc,
  type BarCalendar,
  type CalendarId,
  type ExchangeCalendarId,
  type SessionList,
  type SessionQuery,
  type TradingSession,
} from '@joseph/contracts';

const DAY_MS = 86_400_000;

/**
 * TWSE weekday closures 2024-2027, generated from the engine's
 * `calendar_data/xtai.json` (TWSE FMTQIK and holidaySchedule).
 */
export const DEMO_XTAI_CLOSURES: readonly string[] = [
  '2024-01-01',
  '2024-02-06',
  '2024-02-07',
  '2024-02-08',
  '2024-02-09',
  '2024-02-12',
  '2024-02-13',
  '2024-02-14',
  '2024-02-28',
  '2024-04-04',
  '2024-04-05',
  '2024-05-01',
  '2024-06-10',
  '2024-07-24',
  '2024-07-25',
  '2024-09-17',
  '2024-10-02',
  '2024-10-03',
  '2024-10-10',
  '2024-10-31',
  '2025-01-01',
  '2025-01-23',
  '2025-01-24',
  '2025-01-27',
  '2025-01-28',
  '2025-01-29',
  '2025-01-30',
  '2025-01-31',
  '2025-02-28',
  '2025-04-03',
  '2025-04-04',
  '2025-05-01',
  '2025-05-30',
  '2025-09-29',
  '2025-10-06',
  '2025-10-10',
  '2025-10-24',
  '2025-12-25',
  '2026-01-01',
  '2026-02-12',
  '2026-02-13',
  '2026-02-16',
  '2026-02-17',
  '2026-02-18',
  '2026-02-19',
  '2026-02-20',
  '2026-02-27',
  '2026-04-03',
  '2026-04-06',
  '2026-05-01',
  '2026-06-19',
  '2026-07-10',
  '2026-09-25',
  '2026-09-28',
  '2026-10-09',
  '2026-10-23',
  '2026-10-26',
  '2026-12-25',
  '2027-01-01',
  '2027-02-02',
  '2027-02-03',
  '2027-02-04',
  '2027-02-05',
  '2027-02-08',
  '2027-02-09',
  '2027-02-10',
  '2027-03-01',
  '2027-04-05',
  '2027-04-06',
  '2027-04-30',
  '2027-06-09',
  '2027-09-15',
  '2027-09-28',
  '2027-10-11',
  '2027-10-25',
  '2027-12-24',
  '2027-12-31',
];

/** TWSE closures as day numbers. */
const XTAI_CLOSED: ReadonlySet<number> = new Set(
  DEMO_XTAI_CLOSURES.map(dayNumberOf)
);

/** NYSE ad hoc closures the engine lists. */
const NYSE_SPECIAL_CLOSURES: ReadonlySet<number> = new Set(
  [
    '1994-04-27',
    '2001-09-11',
    '2001-09-12',
    '2001-09-13',
    '2001-09-14',
    '2004-06-11',
    '2007-01-02',
    '2012-10-29',
    '2012-10-30',
    '2018-12-05',
    '2025-01-09',
  ].map(dayNumberOf)
);

/**
 * Day number of a calendar date.
 * @param year Year.
 * @param month Month 1-12.
 * @param day Day of month.
 * @return Days since 1970-01-01.
 */
function dayOf(year: number, month: number, day: number): number {
  return Date.UTC(year, month - 1, day) / DAY_MS;
}

/**
 * Monday-based weekday (Python `date.weekday()`: 0 = Monday).
 * @param day Day number.
 * @return Weekday 0-6.
 */
function isoWeekday(day: number): number {
  return (weekdayOf(day) + 6) % 7;
}

/**
 * The n-th weekday of a month (`n = -1` for the last).
 * @param year Year.
 * @param month Month 1-12.
 * @param weekday 0 = Monday.
 * @param n Occurrence (1-based) or -1.
 * @return Day number.
 */
export function nthWeekday(
  year: number,
  month: number,
  weekday: number,
  n: number
): number {
  if (n > 0) {
    const first = dayOf(year, month, 1);
    const offset = (weekday - isoWeekday(first) + 7) % 7;
    return first + offset + 7 * (n - 1);
  }
  const last = dayOf(year, month + 1, 1) - 1;
  return last - ((isoWeekday(last) - weekday + 7) % 7);
}

/**
 * Western Easter Sunday (anonymous Gregorian algorithm).
 * @param year Year.
 * @return Day number.
 */
export function easterSunday(year: number): number {
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return dayOf(year, month, day);
}

/**
 * Weekend observance of a fixed-date holiday.
 * @param day Holiday date.
 * @param saturdayToFriday Whether a Saturday holiday closes the Friday.
 * @return Closed weekday, or null.
 */
function observed(day: number, saturdayToFriday = true): number | null {
  const weekday = isoWeekday(day);
  if (weekday === 5) return saturdayToFriday ? day - 1 : null;
  if (weekday === 6) return day + 1;
  return day;
}

/** NYSE holidays per year (cached). */
const NYSE_HOLIDAYS = new Map<number, ReadonlySet<number>>();

/**
 * NYSE full-day closures of a year (engine `_nyse_holidays`).
 * @param year Year.
 * @return Closed weekdays.
 */
export function nyseHolidays(year: number): ReadonlySet<number> {
  const cached = NYSE_HOLIDAYS.get(year);
  if (cached) return cached;
  const days: (number | null)[] = [
    observed(dayOf(year, 1, 1), false),
    nthWeekday(year, 2, 0, 3),
    easterSunday(year) - 2,
    nthWeekday(year, 5, 0, -1),
    observed(dayOf(year, 7, 4)),
    nthWeekday(year, 9, 0, 1),
    nthWeekday(year, 11, 3, 4),
    observed(dayOf(year, 12, 25)),
  ];
  if (year >= 1998) days.push(nthWeekday(year, 1, 0, 3));
  if (year >= 2022) days.push(observed(dayOf(year, 6, 19)));
  const result = new Set(days.filter((d): d is number => d !== null));
  NYSE_HOLIDAYS.set(year, result);
  return result;
}

/**
 * NYSE 13:00 early close (engine `_nyse_early_close`).
 * @param day Session date.
 * @return Whether the session closes at 13:00.
 */
export function nyseEarlyClose(day: number): boolean {
  const date = new Date(day * DAY_MS);
  const year = date.getUTCFullYear();
  const month = date.getUTCMonth() + 1;
  const dom = date.getUTCDate();
  if (day === nthWeekday(year, 11, 3, 4) + 1) return true;
  return (
    ((month === 7 && dom === 3) || (month === 12 && dom === 24)) &&
    isoWeekday(day) <= 3
  );
}

/**
 * US Treasury market closures of a year (engine `_bond_holidays`).
 * @param year Year.
 * @return Closed weekdays.
 */
export function bondHolidays(year: number): ReadonlySet<number> {
  const days = new Set(nyseHolidays(year));
  days.delete(easterSunday(year) - 2);
  days.add(nthWeekday(year, 10, 0, 2));
  const veterans = observed(dayOf(year, 11, 11), false);
  if (veterans !== null) days.add(veterans);
  return days;
}

/**
 * Whether a weekday is closed on a calendar.
 * @param calendar Exchange calendar.
 * @param day Day number.
 * @return True when closed.
 */
function closedOn(calendar: ExchangeCalendarId, day: number): boolean {
  const year = new Date(day * DAY_MS).getUTCFullYear();
  if (calendar === 'XTAI') return XTAI_CLOSED.has(day);
  if (calendar === 'USBOND') return bondHolidays(year).has(day);
  return nyseHolidays(year).has(day) || NYSE_SPECIAL_CLOSURES.has(day);
}

/**
 * DEMO session of one local day.
 * @param calendar Exchange calendar.
 * @param day Local day number.
 * @return Session, or null when closed.
 */
export function demoSessionOn(
  calendar: ExchangeCalendarId,
  day: number
): TradingSession | null {
  const weekday = isoWeekday(day);
  if (weekday >= 5 || closedOn(calendar, day)) return null;
  const hours = EXCHANGE_HOURS[calendar];
  const close =
    calendar === 'XNYS' && nyseEarlyClose(day) ? 13 * 60 : hours.closeMinute;
  return {
    day: dayText(day),
    open_ms: zonedTimeToUtc(day, hours.openMinute, hours.timeZone),
    close_ms: zonedTimeToUtc(day, close, hours.timeZone),
  };
}

/**
 * DEMO sessions overlapping a window (engine `sessions(from, to)`).
 * @param query Calendar and window.
 * @return Session list.
 */
export function demoSessionList(query: SessionQuery): SessionList {
  const {calendar, fromMs, toMs} = query;
  const timeZone = EXCHANGE_HOURS[calendar].timeZone;
  const sessions: TradingSession[] = [];
  if (toMs > fromMs) {
    const first = localDayNumber(fromMs, timeZone) - 1;
    const last = localDayNumber(toMs, timeZone) + 1;
    for (let day = first; day <= last; day += 1) {
      const session = demoSessionOn(calendar, day);
      if (session && session.close_ms > fromMs && session.open_ms < toMs)
        sessions.push(session);
    }
  }
  return {calendar, sessions, from_ms: fromMs, to_ms: toMs};
}

/** DEMO calendars per id (rule-based, built lazily). */
const DEMO_CALENDARS = new Map<ExchangeCalendarId, SessionCalendar>();

/**
 * DEMO calendar of an id: the exchange rules above for every day.
 * @param id Calendar id.
 * @return Calendar (`CONTINUOUS_CALENDAR` for `24x7`).
 */
export function demoCalendar(id: CalendarId): BarCalendar {
  if (!isExchangeCalendar(id)) return CONTINUOUS_CALENDAR;
  let calendar = DEMO_CALENDARS.get(id);
  if (!calendar) {
    calendar = new SessionCalendar(EXCHANGE_HOURS[id], {
      fallback: day => demoSessionOn(id, day),
    });
    DEMO_CALENDARS.set(id, calendar);
  }
  return calendar;
}
