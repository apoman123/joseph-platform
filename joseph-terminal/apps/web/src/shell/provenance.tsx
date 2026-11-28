/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Provenance ribbon and badge: the terminal's signature.
 *
 * Every chart pane carries a ribbon on its top edge encoding where its data
 * comes from: solid for live native data, dashed for polled, dotted for
 * replay, red with an age counter when stale, and a diagonal hatch for
 * synthetic DEMO data, which also always shows a "Demo data" label.
 */

import type {ReactElement} from 'react';
import type {ChartFeedState} from './feed-status';
import {formatAge} from './format';

/** Badge labels per feed state. */
const LABELS: Readonly<Record<ChartFeedState, string>> = {
  CLOSED: 'Market closed',
  LIVE: 'Live',
  POLL: 'Polled',
  REPLAY: 'Replay',
  STALE: 'Stale',
  DEMO: 'Demo data',
  UNAVAILABLE: 'Unavailable',
  CONNECTING: 'Connecting',
};

/** Explanations for tooltips and screen readers. */
const DESCRIPTIONS: Readonly<Record<ChartFeedState, string>> = {
  CLOSED:
    'Market closed. Showing last available prices; updates resume during scheduled trading sessions.',
  LIVE: 'Native venue stream, confirmed bars only drive signals.',
  POLL: 'REST polling fallback. Fine for charts, not for order flow or fast automation.',
  REPLAY: 'Recorded history being replayed. Orders cannot be sent from replay.',
  STALE: 'No fresh data. New exposure is blocked until the feed recovers.',
  DEMO: 'Synthetic data generated in the browser. Not a real market.',
  UNAVAILABLE: 'The feed is not available for this venue or instrument.',
  CONNECTING: 'Connecting to the market feed.',
};

/**
 * Returns the badge label of a feed state.
 * @param state Feed state.
 * @param ageMs Data age in ms, shown when stale.
 * @return Label such as `Live` or `Stale for 3 min`.
 */
export function provenanceLabel(state: ChartFeedState, ageMs?: number): string {
  return state === 'STALE' && ageMs !== undefined
    ? `Stale for ${formatAge(ageMs)}`
    : LABELS[state];
}

/**
 * Renders the ribbon along a pane's top edge. It is decorative
 * (`aria-hidden`): the badge carries the same state as text.
 * @param props Feed state.
 * @return Ribbon element.
 */
export function ProvenanceRibbon({
  state,
}: {
  state: ChartFeedState;
}): ReactElement {
  return (
    <div
      className={`ribbon ribbon--${state.toLowerCase()}`}
      data-feed-state={state}
      aria-hidden="true"
    />
  );
}

/**
 * Renders the provenance badge: the label, with the explanation as a
 * tooltip and as visually hidden text for screen readers.
 * @param props Feed state and optional age in ms.
 * @return Badge element.
 */
export function ProvenanceBadge({
  state,
  ageMs,
}: {
  state: ChartFeedState;
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
