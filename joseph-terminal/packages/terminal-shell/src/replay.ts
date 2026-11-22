/**
 * @fileoverview Bar replay model (pure).
 *
 * Replay hides every bar after a cursor and steps forward one closed bar at
 * a time. Indicator records are requested point-in-time (`at_ms` = close of
 * the cursor bar), so nothing from the future is shown. While a chart is in
 * replay its provenance is REPLAY and trading from it is disabled.
 */
import {timeframeEnd, type Timeframe} from '@joseph/contracts';

/** Playback speeds in bars per second. */
export const REPLAY_SPEEDS: readonly number[] = [1, 2, 5, 10];

/** Bars kept before the cursor when replay starts without a chosen bar. */
export const DEFAULT_REPLAY_LOOKBACK = 100;

/** Replay state of one chart. */
export interface ReplayState {
  /** `select`: waiting for the user to click a start bar. */
  phase: 'select' | 'paused' | 'playing';
  /** Start time of the last visible bar, or null before a start is chosen. */
  cursorMs: number | null;
  /** Bars per second while playing. */
  speed: number;
}

/**
 * Enters replay, waiting for a start bar.
 * @return Initial state.
 */
export function startReplay(): ReplayState {
  return {phase: 'select', cursorMs: null, speed: 1};
}

/**
 * Index of the last bar at or before a time.
 * @param times Bar start times, ascending.
 * @param timeMs Time.
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
 * @param times Bar start times, ascending.
 * @param timeMs Clicked time, or null for the default lookback.
 * @return Paused state at the start bar (unchanged when impossible).
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
 * Moves the cursor by whole bars. Reaching the newest bar pauses playback.
 * @param state Current state.
 * @param times Bar start times, ascending.
 * @param delta Bars to move (negative steps back).
 * @return Next state.
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
 * Whether the cursor shows the newest bar (nothing left to replay).
 * @param state State.
 * @param times Bar start times, ascending.
 * @return True at the end.
 */
export function replayAtEnd(
  state: ReplayState,
  times: readonly number[]
): boolean {
  return state.cursorMs !== null && state.cursorMs >= (times.at(-1) ?? 0);
}

/**
 * Bars visible during replay.
 * @param bars Bars, ascending by `timeMs`.
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
 * Point-in-time for backend indicator queries: the close of the cursor bar.
 * Pass the timeframe so calendar months close on the 1st of the next month
 * (28 to 31 days later); a number is a fixed bar duration.
 * @param state Replay state.
 * @param barLength Chart timeframe, or a fixed bar duration in ms.
 * @return `at_ms`, or null when not replaying a chosen bar.
 */
export function replayAsOfMs(
  state: ReplayState | null,
  barLength: Timeframe | number
): number | null {
  if (state?.cursorMs == null) return null;
  return typeof barLength === 'number'
    ? state.cursorMs + barLength
    : timeframeEnd(barLength, state.cursorMs);
}

/**
 * Cycles to the next playback speed.
 * @param state State.
 * @return State with the next speed.
 */
export function nextReplaySpeed(state: ReplayState): ReplayState {
  const index = REPLAY_SPEEDS.indexOf(state.speed);
  const speed = REPLAY_SPEEDS[(index + 1) % REPLAY_SPEEDS.length] ?? 1;
  return {...state, speed};
}
