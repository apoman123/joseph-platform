/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Signal dashboard ranking and triage (pure): sort keys
 * (newest, R multiple, age) and local snooze/dismiss. The R multiple is
 * reward divided by risk.
 *
 * Display only: nothing here changes what the engine decided. Decimal
 * strings become numbers here only to rank and measure, never to show.
 */

import type {Signal} from '@joseph/contracts';

/** Sort keys offered by the alerts panel. */
export type SignalSort = 'newest' | 'rr' | 'age';

/** Sort labels in menu order. */
export const SIGNAL_SORTS: ReadonlyArray<{id: SignalSort; label: string}> = [
  {id: 'newest', label: 'Newest'},
  {id: 'rr', label: 'R multiple'},
  {id: 'age', label: 'Oldest first'},
];

/** Snooze choices in minutes. */
export const SNOOZE_MINUTES: readonly number[] = [15, 60, 240];

/** Local triage of signals (persisted in feature settings). */
export interface SignalTriage {
  /** Dismissed signal ids, newest first (hidden unless shown on request). */
  dismissed: readonly string[];
  /** Snoozed signal id to wake-up time (UTC ms). */
  snoozed: Readonly<Record<string, number>>;
}

/**
 * Returns a signal's R multiple as a number, for ranking.
 * @param signal Signal.
 * @return Number, or null when absent or not a finite number.
 */
function rrOf(signal: Signal): number | null {
  const value = signal.rr == null ? NaN : Number(signal.rr);
  return Number.isFinite(value) ? value : null;
}

/**
 * Compares nullable numbers, keeping nulls last in either direction.
 * @param a First value.
 * @param b Second value.
 * @param descending Whether larger values come first.
 * @return Comparator result.
 */
function byNumber(
  a: number | null,
  b: number | null,
  descending: boolean
): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return descending ? b - a : a - b;
}

/**
 * Sorts signals for display; ties fall back to newest first. Signals
 * without an R multiple sort last.
 * @param signals Signals.
 * @param sort Sort key.
 * @return Sorted copy.
 */
export function sortSignals(
  signals: readonly Signal[],
  sort: SignalSort
): Signal[] {
  const newest = (a: Signal, b: Signal) =>
    b.bar_close_ms - a.bar_close_ms || b.evaluated_at_ms - a.evaluated_at_ms;
  return [...signals].sort((a, b) => {
    let result = 0;
    if (sort === 'rr') result = byNumber(rrOf(a), rrOf(b), true);
    else if (sort === 'age') result = a.bar_close_ms - b.bar_close_ms;
    return result || newest(a, b);
  });
}

/**
 * Returns whether a signal is hidden by a snooze right now.
 * @param triage Triage state.
 * @param signalId Signal id.
 * @param nowMs Current time (UTC ms).
 * @return True until the snooze's wake-up time.
 */
export function isSnoozed(
  triage: SignalTriage,
  signalId: string,
  nowMs: number
): boolean {
  const until = triage.snoozed[signalId];
  return until !== undefined && until > nowMs;
}

/**
 * Snoozes a signal, dropping expired snoozes so the settings stay small.
 * @param triage Triage state.
 * @param signalId Signal id.
 * @param minutes Duration in minutes.
 * @param nowMs Current time (UTC ms).
 * @return Next triage state.
 */
export function snooze(
  triage: SignalTriage,
  signalId: string,
  minutes: number,
  nowMs: number
): SignalTriage {
  const snoozed: Record<string, number> = {};
  for (const [id, until] of Object.entries(triage.snoozed)) {
    if (until > nowMs) snoozed[id] = until;
  }
  snoozed[signalId] = nowMs + minutes * 60_000;
  return {...triage, snoozed};
}

/**
 * Dismisses or restores a signal. Only the newest 500 dismissed ids are
 * kept, so the settings stay bounded.
 * @param triage Triage state.
 * @param signalId Signal id.
 * @param dismissed Whether to dismiss.
 * @return Next triage state.
 */
export function setDismissed(
  triage: SignalTriage,
  signalId: string,
  dismissed: boolean
): SignalTriage {
  const others = triage.dismissed.filter(id => id !== signalId);
  return {
    ...triage,
    dismissed: dismissed ? [signalId, ...others].slice(0, 500) : others,
  };
}

/**
 * Formats the time left on a snooze.
 * @param untilMs Wake-up time (UTC ms).
 * @param nowMs Current time (UTC ms).
 * @return Text such as `for 42 min` or `for 1.5 h`; at least one minute.
 */
export function snoozeLabel(untilMs: number, nowMs: number): string {
  const minutes = Math.max(1, Math.round((untilMs - nowMs) / 60_000));
  return minutes >= 60
    ? `for ${Math.round(minutes / 6) / 10} h`
    : `for ${minutes} min`;
}
