/**
 * @fileoverview Provenance ribbon and badge: the terminal's signature.
 *
 * Every chart pane carries a ribbon on its top edge encoding where its data
 * comes from: solid for live native data, dashed for polled, dotted for
 * replay, red with an age counter when stale, and a diagonal hatch for
 * synthetic DEMO data, which also always shows a "Demo data" label.
 */
import type {ReactElement} from 'react';
import type {FeedState} from '@joseph/contracts';
import {formatAge} from './format';

/** Human labels. */
const LABELS: Readonly<Record<FeedState, string>> = {
  LIVE: 'Live',
  POLL: 'Polled',
  REPLAY: 'Replay',
  STALE: 'Stale',
  DEMO: 'Demo data',
  UNAVAILABLE: 'Unavailable',
  CONNECTING: 'Connecting',
};

/** Explanations for tooltips and screen readers. */
const DESCRIPTIONS: Readonly<Record<FeedState, string>> = {
  LIVE: 'Native venue stream, confirmed bars only drive signals.',
  POLL: 'REST polling fallback. Fine for charts, not for order flow or fast automation.',
  REPLAY: 'Recorded history being replayed. Orders cannot be sent from replay.',
  STALE: 'No fresh data. New exposure is blocked until the feed recovers.',
  DEMO: 'Synthetic data generated in the browser. Not a real market.',
  UNAVAILABLE: 'The feed is not available for this venue or instrument.',
  CONNECTING: 'Connecting to the market feed.',
};

/**
 * Label for a feed state.
 * @param state Feed state.
 * @param ageMs Data age, shown when stale.
 * @return Label.
 */
export function provenanceLabel(state: FeedState, ageMs?: number): string {
  return state === 'STALE' && ageMs !== undefined
    ? `Stale for ${formatAge(ageMs)}`
    : LABELS[state];
}

/**
 * Ribbon along a pane's top edge.
 * @param props Feed state.
 * @return Ribbon.
 */
export function ProvenanceRibbon({state}: {state: FeedState}): ReactElement {
  return (
    <div
      className={`ribbon ribbon--${state.toLowerCase()}`}
      data-feed-state={state}
      aria-hidden="true"
    />
  );
}

/**
 * Badge with label and explanation.
 * @param props Feed state and optional age.
 * @return Badge.
 */
export function ProvenanceBadge({
  state,
  ageMs,
}: {
  state: FeedState;
  ageMs?: number;
}): ReactElement {
  return (
    <span
      className={`j-badge j-badge--${state.toLowerCase()} provenance-badge`}
      title={DESCRIPTIONS[state]}
      data-testid="provenance-badge"
    >
      {provenanceLabel(state, ageMs)}
      <span className="sr-only">. {DESCRIPTIONS[state]}</span>
    </span>
  );
}
