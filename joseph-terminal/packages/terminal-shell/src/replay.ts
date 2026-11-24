/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Bar replay model (pure).
 *
 * Replay hides every bar after a cursor and steps forward one closed bar at
 * a time. Indicator records are requested point-in-time (`at_ms` = close of
 * the cursor bar), so nothing from the future is shown. While a chart is in
 * replay its provenance is REPLAY and trading from it is disabled.
 */

import {
  timeframeEnd,
  type BarCalendar,
  type Timeframe,
} from '@joseph/contracts';

/** Playback speeds in bars per second. */
export const REPLAY_SPEEDS: readonly number[] = [1, 2, 5, 10];

/** Bars kept before the cursor when replay starts without a chosen bar. */
export const DEFAULT_REPLAY_LOOKBACK = 100;

/** Replay state of one chart. */
export interface ReplayState {
  /** `select`: waiting for the user to click a start bar. */
  phase: 'select' | 'paused' | 'playing';
  /**
   * Start time (UTC epoch ms) of the last visible bar, or null before a
   * start is chosen.
   */
  cursorMs: number | null;
  /** Bars per second while playing. */
  speed: number;
}

/**
 * Enters replay, waiting for a start bar.
 * @return Initial state: `select` phase at 1 bar per second.
 */
export function startReplay(): ReplayState {
  return {phase: 'select', cursorMs: null, speed: 1};
}

/**
 * Returns the index of the last bar starting at or before a time (binary
 * search).
 * @param times Bar start times (epoch ms), ascending.
 * @param timeMs Time to look up (epoch ms).
 * @return Index, or -1 when every bar is later.
 */
export function lastIndexAtOrBefore(
  times: readonly number[],
  timeMs: number
): number {
  let low = 0;
  let high = times.length - 1;
  let found = -1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if ((times[mid] ?? Infinity) <= timeMs) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found;
}

/**
 * Chooses the start bar (snapped to a bar at or before the clicked time).
 * At least one bar must remain hidden, otherwise there is nothing to replay.
 * @param state Current state.
 * @param times Bar start times (epoch ms), ascending.
 * @param timeMs Clicked time (epoch ms), or null for
 *     `DEFAULT_REPLAY_LOOKBACK` bars before the newest.
 * @return Paused state at the start bar; `state` itself when there are
 *     fewer than two bars.
 */
export function chooseReplayStart(
  state: ReplayState,
  times: readonly number[],
  timeMs: number | null
): ReplayState {
  if (times.length < 2) return state;
  const index =
    timeMs === null
      ? Math.max(0, times.length - 1 - DEFAULT_REPLAY_LOOKBACK)
      : Math.min(
          times.length - 2,
          Math.max(0, lastIndexAtOrBefore(times, timeMs))
        );
  const cursorMs = times[index];
  return cursorMs === undefined ? state : {...state, phase: 'paused', cursorMs};
}

/**
 * Moves the cursor by whole bars, clamped to the first and newest bar.
 * Reaching the newest bar pauses playback.
 * @param state Current state.
 * @param times Bar start times (epoch ms), ascending.
 * @param delta Bars to move (negative steps back).
 * @return Next state; `state` itself before a start is chosen or without
 *     bars.
 */
export function stepReplay(
  state: ReplayState,
  times: readonly number[],
  delta: number
): ReplayState {
  if (state.cursorMs === null || times.length === 0) return state;
  const current = lastIndexAtOrBefore(times, state.cursorMs);
  const index = Math.min(times.length - 1, Math.max(0, current + delta));
  const cursorMs = times[index] ?? state.cursorMs;
  const atEnd = index === times.length - 1;
  return {
    ...state,
    cursorMs,
    phase: atEnd && state.phase === 'playing' ? 'paused' : state.phase,
  };
}

/**
 * Returns whether the cursor shows the newest bar (nothing left to replay).
 * @param state Replay state.
 * @param times Bar start times (epoch ms), ascending.
 * @return True at or past the newest bar; false before a start is chosen.
 */
export function replayAtEnd(
  state: ReplayState,
  times: readonly number[]
): boolean {
  return state.cursorMs !== null && state.cursorMs >= (times.at(-1) ?? 0);
}

/**
 * Returns the bars visible during replay.
 * @param bars Bars, ascending by `timeMs` (bar start, epoch ms).
 * @param state Replay state (null = not replaying).
 * @return Bars up to and including the cursor (all bars when not replaying
 *     or while choosing the start).
 */
export function replayVisibleBars<T extends {timeMs: number}>(
  bars: readonly T[],
  state: ReplayState | null
): readonly T[] {
  if (!state || state.cursorMs === null) return bars;
  const cursor = state.cursorMs;
  return bars.filter(bar => bar.timeMs <= cursor);
}

/**
 * Returns the point-in-time for backend indicator queries: the close of the
 * cursor bar. Pass the timeframe so calendar months close on the 1st of the
 * next month (28 to 31 days later); a number is a fixed bar duration. With a
 * session calendar the close is the calendar's bar end (the session close
 * for the last bar of a session, the last session of the week or month).
 * @param state Replay state (null = not replaying).
 * @param barLength Chart timeframe, or a fixed bar duration in ms (the
 *     calendar is then ignored).
 * @param calendar Session calendar of the instrument (default UTC bars).
 * @return `at_ms` (UTC epoch ms, exclusive bar end), or null when not
 *     replaying a chosen bar.
 */
export function replayAsOfMs(
  state: ReplayState | null,
  barLength: Timeframe | number,
  calendar?: BarCalendar | null
): number | null {
  if (state?.cursorMs == null) return null;
  if (typeof barLength === 'number') return state.cursorMs + barLength;
  return calendar
    ? calendar.endOf(barLength, state.cursorMs)
    : timeframeEnd(barLength, state.cursorMs);
}

/**
 * Cycles to the next playback speed in `REPLAY_SPEEDS`, wrapping around.
 * @param state Replay state.
 * @return State with the next speed; an unknown speed restarts at the first.
 */
export function nextReplaySpeed(state: ReplayState): ReplayState {
  const index = REPLAY_SPEEDS.indexOf(state.speed);
  const speed = REPLAY_SPEEDS[(index + 1) % REPLAY_SPEEDS.length] ?? 1;
  return {...state, speed};
}
