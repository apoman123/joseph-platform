/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Session calendars in the shell: a hook that follows the
 * session cache, and the market status badge (open/closed, next open,
 * holiday and early-close notices) shown on charts of session markets.
 */

'use client';
import {
  marketStatus,
  zonedParts,
  type BarCalendar,
  type CalendarId,
  type MarketStatus,
  type TradingSession,
} from '@joseph/contracts';
import {zoneAbbreviation, zonedTickLabel} from '@joseph/chart-core';
import {useEffect, useState, type ReactElement} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {formatCountdown} from '@joseph/terminal-shell';

/** Sessions kept loaded on each side of now (21 days) for status lines. */
const NOW_WINDOW_MS = 21 * 86_400_000;

/**
 * Returns the current calendar of an id. The component re-renders when the
 * engine's (or DEMO's) sessions for it arrive; the weeks around now are
 * loaded on mount.
 * @param runtime Runtime whose session cache is used.
 * @param calendarId Calendar id such as `XNYS` or `24x7`.
 * @return Calendar.
 */
export function useBarCalendar(
  runtime: TerminalRuntime,
  calendarId: CalendarId
): BarCalendar {
  const [calendar, setCalendar] = useState(() =>
    runtime.calendars.calendar(calendarId)
  );
  useEffect(() => {
    setCalendar(runtime.calendars.calendar(calendarId));
    const off = runtime.calendars.subscribe(id => {
      if (id === calendarId) setCalendar(runtime.calendars.calendar(id));
    });
    const now = Date.now();
    runtime.calendars.prefetch(
      calendarId,
      now - NOW_WINDOW_MS,
      now + NOW_WINDOW_MS,
      /* marginMs= */ 0
    );
    return off;
  }, [runtime, calendarId]);
  return calendar;
}

/** Weekday names in exchange time. */
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

/**
 * Formats a session time such as `Mon 09:30 ET`.
 * @param ms Instant, UTC ms.
 * @param calendar Calendar whose time zone is used.
 * @param withDay Whether to prefix the exchange-local weekday.
 * @return Text in exchange time.
 */
function sessionTime(
  ms: number,
  calendar: BarCalendar,
  withDay: boolean
): string {
  const zone = calendar.timeZone;
  const p = zonedParts(ms, zone);
  const weekday =
    WEEKDAYS[new Date(Date.UTC(p.year, p.month - 1, p.day)).getUTCDay()] ?? '';
  const time = zonedTickLabel(ms, 'time', zone);
  return `${withDay ? `${weekday} ` : ''}${time} ${zoneAbbreviation(zone)}`;
}

/** Display of a market status. */
export interface MarketStatusView {
  /** `open` or `closed` (data attribute). */
  state: 'open' | 'closed';
  /** Badge text such as `Open` or `Closed`. */
  label: string;
  /** Detail such as `closes 16:00 ET` or `opens Mon 09:30 ET`. */
  detail: string;
  /** Notice for holidays and early closes, if any. */
  notice: string | null;
}

/**
 * Builds the badge text of a status. Within 12 hours of the next open the
 * detail adds a countdown.
 * @param status Market status.
 * @param calendar Calendar of the market (exchange time zone).
 * @param nowMs Current time, UTC ms.
 * @param market Market name for notices, e.g. `NYSE / Nasdaq`.
 * @return Label, detail and notice of the badge.
 */
export function describeMarketStatus(
  status: MarketStatus,
  calendar: BarCalendar,
  nowMs: number,
  market: string
): MarketStatusView {
  const opensText = (next: TradingSession | null) => {
    if (!next) return 'next session unknown';
    const soon = next.open_ms - nowMs < 12 * 3_600_000;
    return soon
      ? `opens ${sessionTime(next.open_ms, calendar, false)} (in ${formatCountdown(next.open_ms - nowMs)})`
      : `opens ${sessionTime(next.open_ms, calendar, true)}`;
  };
  let notice: string | null = null;
  if (status.holiday) notice = `${market} holiday today`;
  else if (status.earlyClose) {
    const close = status.session?.close_ms ?? null;
    notice = close
      ? `Early close today at ${sessionTime(close, calendar, false)}`
      : 'Early close today';
  }
  if (status.open && status.session) {
    return {
      state: 'open',
      label: 'Open',
      detail: `closes ${sessionTime(status.session.close_ms, calendar, false)}`,
      notice,
    };
  }
  return {
    state: 'closed',
    label: status.holiday ? 'Holiday' : 'Closed',
    detail: opensText(status.next),
    notice,
  };
}

/** Props of the badge. */
interface MarketStatusBadgeProps {
  /** Session calendar of the instrument. */
  calendar: BarCalendar;
  /** Market name for notices. */
  market: string;
  /** Current time, UTC ms; the parent re-renders the badge as it ticks. */
  nowMs: number;
}

/**
 * Renders the market status badge of a session market. The full status is
 * in the tooltip; narrow panes hide the detail and notice.
 * @param props Calendar, market and clock.
 * @return Badge, or null for markets that never close (`24x7`).
 */
export function MarketStatusBadge({
  calendar,
  market,
  nowMs,
}: MarketStatusBadgeProps): ReactElement | null {
  if (calendar.continuous) return null;
  const view = describeMarketStatus(
    marketStatus(calendar, nowMs),
    calendar,
    nowMs,
    market
  );
  return (
    <span
      className={`pane__chip market-status market-status--${view.state}`}
      data-testid="market-status"
      data-state={view.state}
      title={`${market}: ${view.label}, ${view.detail}${view.notice ? `. ${view.notice}` : ''}. Bars cover sessions only; times are exchange time.`}
    >
      <span className="market-status__dot" aria-hidden="true" />
      <span>{view.label}</span>
      <span className="market-status__detail">{view.detail}</span>
      {view.notice && (
        <span
          className="market-status__notice"
          data-testid="market-status-notice"
        >
          {view.notice}
        </span>
      )}
    </span>
  );
}
