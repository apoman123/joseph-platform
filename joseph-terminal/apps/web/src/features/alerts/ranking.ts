/**
 * @fileoverview Signal dashboard ranking and triage (pure): sort keys
 * (newest, R multiple, confluence, age, distance to the AOI), confluence
 * counts from the reason tree, AOI distance and local snooze/dismiss.
 * Display only: nothing here changes what the engine decided.
 */
import type {AoiRecord, ReasonNode, Signal} from '@joseph/contracts';

/** Sort keys offered by the alerts panel. */
export type SignalSort = 'newest' | 'rr' | 'confluence' | 'age' | 'distance';

/** Sort labels in menu order. */
export const SIGNAL_SORTS: readonly {id: SignalSort; label: string}[] = [
  {id: 'newest', label: 'Newest'},
  {id: 'rr', label: 'R multiple'},
  {id: 'confluence', label: 'Confluence'},
  {id: 'distance', label: 'Distance to AOI'},
  {id: 'age', label: 'Oldest first'},
];

/** Snooze choices in minutes. */
export const SNOOZE_MINUTES: readonly number[] = [15, 60, 240];

/** Local triage of signals (persisted in feature settings). */
export interface SignalTriage {
  /** Dismissed signal ids (hidden until "show dismissed"). */
  dismissed: readonly string[];
  /** Snoozed signal id to wake-up time (UTC ms). */
  snoozed: Readonly<Record<string, number>>;
}

/**
 * Counts satisfied leaf conditions in a reason tree.
 * @param node Root node.
 * @return Number of TRUE leaves.
 */
export function confluenceOf(node: ReasonNode): number {
  if (node.children.length === 0) return node.result === 'TRUE' ? 1 : 0;
  return node.children.reduce((sum, child) => sum + confluenceOf(child), 0);
}

/**
 * Distance from a price to the nearest of the given AOIs, in percent of
 * the price; zero when the price is inside one.
 * @param price Latest price.
 * @param aois AOIs referenced by the signal.
 * @return Percent distance, or null without price or AOIs.
 */
export function aoiDistancePct(
  price: number | null,
  aois: readonly Pick<AoiRecord, 'lower' | 'upper'>[]
): number | null {
  if (price === null || !Number.isFinite(price) || price <= 0) return null;
  let best: number | null = null;
  for (const aoi of aois) {
    const lower = Number(aoi.lower);
    const upper = Number(aoi.upper);
    if (!Number.isFinite(lower) || !Number.isFinite(upper)) continue;
    const distance =
      price >= lower && price <= upper
        ? 0
        : Math.min(Math.abs(price - lower), Math.abs(price - upper));
    const pct = (distance / price) * 100;
    best = best === null ? pct : Math.min(best, pct);
  }
  return best;
}

/**
 * Parses a decimal string R multiple.
 * @param signal Signal.
 * @return Number or null.
 */
function rrOf(signal: Signal): number | null {
  const value = signal.rr == null ? NaN : Number(signal.rr);
  return Number.isFinite(value) ? value : null;
}

/**
 * Compares nullable numbers, keeping nulls last.
 * @param a First.
 * @param b Second.
 * @param descending Larger first.
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
 * Sorts signals for display (stable; ties fall back to newest).
 * @param signals Signals.
 * @param sort Sort key.
 * @param distances Distance to AOI by signal id (percent).
 * @return Sorted copy.
 */
export function sortSignals(
  signals: readonly Signal[],
  sort: SignalSort,
  distances: Readonly<Record<string, number | null>> = {}
): Signal[] {
  const newest = (a: Signal, b: Signal) =>
    b.bar_close_ms - a.bar_close_ms || b.evaluated_at_ms - a.evaluated_at_ms;
  return [...signals].sort((a, b) => {
    let result = 0;
    if (sort === 'rr') result = byNumber(rrOf(a), rrOf(b), true);
    else if (sort === 'confluence')
      result = confluenceOf(b.reason_tree) - confluenceOf(a.reason_tree);
    else if (sort === 'age') result = a.bar_close_ms - b.bar_close_ms;
    else if (sort === 'distance')
      result = byNumber(
        distances[a.signal_id] ?? null,
        distances[b.signal_id] ?? null,
        false
      );
    return result || newest(a, b);
  });
}

/**
 * Whether a signal is hidden by snooze right now.
 * @param triage Triage state.
 * @param signalId Signal id.
 * @param nowMs Clock.
 * @return True while snoozed.
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
 * Snoozes a signal, dropping expired snoozes.
 * @param triage Triage state.
 * @param signalId Signal id.
 * @param minutes Duration.
 * @param nowMs Clock.
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
 * Dismisses or restores a signal (kept to the newest 500 ids).
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
 * Formats a snooze wake-up time.
 * @param untilMs Wake-up (UTC ms).
 * @param nowMs Clock.
 * @return Text such as `for 42 min`.
 */
export function snoozeLabel(untilMs: number, nowMs: number): string {
  const minutes = Math.max(1, Math.round((untilMs - nowMs) / 60_000));
  return minutes >= 60
    ? `for ${Math.round(minutes / 6) / 10} h`
    : `for ${minutes} min`;
}
