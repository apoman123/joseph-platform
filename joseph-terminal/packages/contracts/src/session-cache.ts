/**
 * @fileoverview Session-list cache: loads exchange sessions from the market
 * engine (`GET /v1/calendars/{calendar_id}/sessions`) or the DEMO provider
 * and serves calendars built from them.
 *
 * Sessions are fetched in fixed, epoch-aligned chunks (so overlapping
 * requests share them) and merged per calendar; adjacent missing chunks go
 * out as one request of at most `MAX_REQUEST_DAYS` (the engine answers up to
 * about 400 days). `calendar(id)` always answers synchronously: days not
 * loaded yet follow the regular weekday hours, and subscribers are told
 * when better data arrives so charts recompute their bar boundaries.
 */
import type {SessionQuery} from './client/ports';
import type {
  CalendarId,
  SessionList,
  TradingSession,
} from './generated/contracts';
import {
  CONTINUOUS_CALENDAR,
  createBarCalendar,
  isExchangeCalendar,
  type BarCalendar,
  type ExchangeCalendarId,
  type TimeWindow,
} from './sessions';

const DAY_MS = 86_400_000;

/** Fetch granularity: sessions are loaded in 64-day, epoch-aligned chunks. */
export const SESSION_CHUNK_MS = 64 * DAY_MS;

/** Longest single request (the engine serves windows up to ~400 days). */
export const MAX_REQUEST_DAYS = 384;

/** Wait before retrying a failed chunk. */
export const SESSION_RETRY_MS = 30_000;

/** Margin loaded on both sides of a prefetched range. */
export const PREFETCH_MARGIN_MS = 30 * DAY_MS;

/**
 * Loads one session list.
 * @param query Calendar and window.
 * @return Sessions overlapping the window.
 */
export type SessionFetcher = (query: SessionQuery) => Promise<SessionList>;

/** Cache options. */
export interface SessionCacheOptions {
  /** Clock (retry back-off). */
  now?: () => number;
}

/** Loaded state of one calendar. */
interface CalendarState {
  /** Sessions by local day. */
  sessions: Map<string, TradingSession>;
  /** Loaded chunk indexes. */
  chunks: Set<number>;
  /** In-flight chunk loads. */
  pending: Map<number, Promise<void>>;
  /** Chunk index to the time a retry is allowed. */
  failed: Map<number, number>;
  /** Calendar built from the loaded sessions. */
  calendar: BarCalendar;
}

/**
 * Merges sorted chunk indexes into contiguous windows.
 * @param chunks Chunk indexes.
 * @return Windows `[fromMs, toMs)`.
 */
function chunkWindows(chunks: Iterable<number>): TimeWindow[] {
  const sorted = [...chunks].sort((a, b) => a - b);
  const windows: TimeWindow[] = [];
  for (const chunk of sorted) {
    const fromMs = chunk * SESSION_CHUNK_MS;
    const last = windows.at(-1);
    if (last && last.toMs === fromMs) last.toMs = fromMs + SESSION_CHUNK_MS;
    else windows.push({fromMs, toMs: fromMs + SESSION_CHUNK_MS});
  }
  return windows;
}

/** Session calendars per calendar id, loaded on demand. */
export class SessionCalendarCache {
  private readonly states = new Map<ExchangeCalendarId, CalendarState>();
  private readonly listeners = new Set<(id: CalendarId) => void>();
  private readonly now: () => number;

  /**
   * Creates the cache.
   * @param fetcher Session source, or null to use regular hours only.
   * @param options Clock.
   */
  constructor(
    private readonly fetcher: SessionFetcher | null,
    options: SessionCacheOptions = {}
  ) {
    this.now = options.now ?? (() => Date.now());
  }

  /**
   * State of an exchange calendar (created on first use).
   * @param id Calendar id.
   * @return State.
   */
  private state(id: ExchangeCalendarId): CalendarState {
    let state = this.states.get(id);
    if (!state) {
      state = {
        sessions: new Map(),
        chunks: new Set(),
        pending: new Map(),
        failed: new Map(),
        calendar: createBarCalendar(id),
      };
      this.states.set(id, state);
    }
    return state;
  }

  /**
   * Best calendar known now (never waits).
   * @param id Calendar id.
   * @return Calendar.
   */
  calendar(id: CalendarId): BarCalendar {
    return isExchangeCalendar(id)
      ? this.state(id).calendar
      : CONTINUOUS_CALENDAR;
  }

  /**
   * Whether a window is fully loaded.
   * @param id Calendar id.
   * @param fromMs Start.
   * @param toMs End.
   * @return True for `24x7` or when every chunk is loaded.
   */
  covered(id: CalendarId, fromMs: number, toMs: number): boolean {
    if (!isExchangeCalendar(id)) return true;
    const state = this.state(id);
    for (const chunk of this.chunkRange(fromMs, toMs)) {
      if (!state.chunks.has(chunk)) return false;
    }
    return true;
  }

  /**
   * Chunk indexes overlapping a window.
   * @param fromMs Start.
   * @param toMs End.
   * @return Indexes.
   */
  private chunkRange(fromMs: number, toMs: number): number[] {
    const first = Math.floor(fromMs / SESSION_CHUNK_MS);
    const last = Math.floor(
      (Math.max(fromMs + 1, toMs) - 1) / SESSION_CHUNK_MS
    );
    const chunks: number[] = [];
    for (let chunk = first; chunk <= last; chunk += 1) chunks.push(chunk);
    return chunks;
  }

  /**
   * Loads every missing chunk of a window, then resolves with the updated
   * calendar. Failures leave the regular-hours fallback in place.
   * @param id Calendar id.
   * @param fromMs Start.
   * @param toMs End.
   * @return Calendar.
   */
  async ensure(
    id: CalendarId,
    fromMs: number,
    toMs: number
  ): Promise<BarCalendar> {
    if (!isExchangeCalendar(id) || !this.fetcher) return this.calendar(id);
    const state = this.state(id);
    const now = this.now();
    const waits: Promise<void>[] = [];
    let run: number[] = [];
    const flush = () => {
      if (run.length > 0) waits.push(this.load(id, state, run));
      run = [];
    };
    const maxChunks = Math.max(
      1,
      Math.floor((MAX_REQUEST_DAYS * DAY_MS) / SESSION_CHUNK_MS)
    );
    for (const chunk of this.chunkRange(fromMs, toMs)) {
      const pending = state.pending.get(chunk);
      const blocked = (state.failed.get(chunk) ?? -Infinity) > now;
      if (state.chunks.has(chunk) || pending || blocked) {
        flush();
        if (pending) waits.push(pending);
        continue;
      }
      run.push(chunk);
      if (run.length >= maxChunks) flush();
    }
    flush();
    await Promise.all(waits);
    return state.calendar;
  }

  /**
   * Starts loading a window plus a margin on both sides (fire and forget).
   * @param id Calendar id.
   * @param fromMs Start.
   * @param toMs End.
   * @param marginMs Extra time on both sides.
   */
  prefetch(
    id: CalendarId,
    fromMs: number,
    toMs: number,
    marginMs = PREFETCH_MARGIN_MS
  ): void {
    void this.ensure(id, fromMs - marginMs, toMs + marginMs).catch(
      () => undefined
    );
  }

  /**
   * Loads a contiguous run of chunks with one request.
   * @param id Calendar id.
   * @param state Calendar state.
   * @param chunks Contiguous chunk indexes.
   * @return Promise settling when the response is merged.
   */
  private load(
    id: ExchangeCalendarId,
    state: CalendarState,
    chunks: readonly number[]
  ): Promise<void> {
    const first = chunks[0] ?? 0;
    const last = chunks.at(-1) ?? first;
    const fetcher = this.fetcher;
    const promise = (
      fetcher
        ? fetcher({
            calendar: id,
            fromMs: first * SESSION_CHUNK_MS,
            toMs: (last + 1) * SESSION_CHUNK_MS,
          })
        : Promise.reject(new Error('no session source'))
    )
      .then(list => {
        for (const session of list.sessions) {
          state.sessions.set(session.day, session);
        }
        for (const chunk of chunks) {
          state.chunks.add(chunk);
          state.failed.delete(chunk);
        }
        state.calendar = createBarCalendar(id, {
          sessions: [...state.sessions.values()],
          coverage: chunkWindows(state.chunks),
        });
        this.emit(id);
      })
      .catch(() => {
        const retryAt = this.now() + SESSION_RETRY_MS;
        for (const chunk of chunks) state.failed.set(chunk, retryAt);
      })
      .finally(() => {
        for (const chunk of chunks) {
          if (state.pending.get(chunk) === promise) state.pending.delete(chunk);
        }
      });
    for (const chunk of chunks) state.pending.set(chunk, promise);
    return promise;
  }

  /**
   * Subscribes to calendar updates.
   * @param listener Receives the calendar id whose sessions changed.
   * @return Unsubscribe.
   */
  subscribe(listener: (id: CalendarId) => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Notifies listeners.
   * @param id Calendar id.
   */
  private emit(id: CalendarId): void {
    for (const listener of [...this.listeners]) {
      try {
        listener(id);
      } catch {
        // A faulty listener must not stop the others.
      }
    }
  }
}
