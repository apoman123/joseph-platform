/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Catalog of rule nodes for the "add condition" menu, with
 * default factories. Entries that need indicator outputs (states, zones,
 * events) are offered only when the vocabulary has them, and their
 * defaults come from the vocabulary.
 */

import type {Direction, RuleNode, Timeframe} from '@joseph/contracts';
import {stateValues, type Vocabulary} from './vocabulary';

/** Menu categories. */
export type NodeCategory =
  | 'Logic'
  | 'Indicator state'
  | 'Zones and levels'
  | 'Events'
  | 'Values'
  | 'Time'
  | 'Risk';

/** Catalog entry. */
export interface CatalogEntry {
  /** Node operator. */
  op: RuleNode['op'];
  /** Menu label. */
  title: string;
  /** One-line explanation. */
  description: string;
  /** Category. */
  category: NodeCategory;
  /**
   * Returns whether the installed plugins can feed this node.
   * @param vocab Vocabulary of the installed plugins.
   * @return True when the menu should offer the entry.
   */
  available(vocab: Vocabulary): boolean;
  /**
   * Creates a default node for the strategy direction.
   * @param direction Strategy direction.
   * @param signalTf Signal timeframe (TF).
   * @param vocab Vocabulary the defaults are taken from.
   * @return New node without an id.
   */
  create(
    direction: Direction,
    signalTf: Timeframe,
    vocab: Vocabulary
  ): RuleNode;
}

/**
 * Returns the zone side matching a direction.
 * @param direction Strategy direction.
 * @return `bullish` for LONG, `bearish` for SHORT.
 */
export function sideFor(direction: Direction): 'bullish' | 'bearish' {
  return direction === 'LONG' ? 'bullish' : 'bearish';
}

/**
 * Returns the default child of a new group or wrapper: the close above the
 * EMA for a long, below it for a short.
 * @param direction Strategy direction.
 * @return Comparison node.
 */
export function defaultLeaf(direction: Direction): RuleNode {
  return {
    op: 'COMPARE',
    left: {kind: 'candle', field: 'close'},
    cmp: direction === 'LONG' ? 'gt' : 'lt',
    right: {kind: 'indicator', plugin_id: 'ema', field: 'ema'},
  };
}

/**
 * Finds the first value a STATE condition can match.
 * @param vocab Vocabulary.
 * @return Plugin id and the value spec, or null when no installed plugin
 *     publishes a string or boolean value.
 */
function firstStateValue(
  vocab: Vocabulary
): {pluginId: string; key: string; value: string | boolean} | null {
  for (const info of vocab.plugins.values()) {
    const [spec] = stateValues(info);
    if (!spec) continue;
    const value = spec.type === 'boolean' ? true : (spec.choices?.[0] ?? '');
    return {pluginId: info.plugin_id, key: spec.key, value};
  }
  return null;
}

/**
 * Lists the first AOI kinds of the vocabulary.
 * @param vocab Vocabulary.
 * @param count Number of kinds wanted.
 * @return Up to `count` kinds in registration order.
 */
function firstKinds(vocab: Vocabulary, count: number): string[] {
  return [...vocab.kinds.keys()].slice(0, count);
}

/**
 * Lists the first event names of the vocabulary.
 * @param vocab Vocabulary.
 * @param count Number of names wanted.
 * @return Up to `count` names, declared events before reserved ones.
 */
function firstEvents(vocab: Vocabulary, count: number): string[] {
  return [...vocab.events.keys()].slice(0, count);
}

/**
 * Creates the default STATE node: the first matchable value of the first
 * plugin that publishes one.
 * @param tf Timeframe to read.
 * @param vocab Vocabulary.
 * @return STATE node (with an empty plugin id when nothing is matchable).
 */
function stateNode(tf: Timeframe, vocab: Vocabulary): RuleNode {
  const first = firstStateValue(vocab);
  return {
    op: 'STATE',
    plugin_id: first?.pluginId ?? '',
    timeframes: [tf],
    where: [{field: first?.key ?? '', any_of: [first?.value ?? '']}],
  };
}

/**
 * Creates the default SEQUENCE node: the first two events, or the first
 * event twice.
 * @param vocab Vocabulary.
 * @return SEQUENCE node.
 */
function sequenceNode(vocab: Vocabulary): RuleNode {
  const [first = '', second = first] = firstEvents(vocab, 2);
  return {
    op: 'SEQUENCE',
    within_bars: 4,
    steps: [{name: first}, {name: second}],
  };
}

/**
 * Returns whether the vocabulary has at least one entry.
 * @param map Vocabulary map.
 * @return True when non-empty.
 */
function any(map: ReadonlyMap<string, unknown>): boolean {
  return map.size > 0;
}

/**
 * Returns a value that is always offered.
 * @return True.
 */
function always(): boolean {
  return true;
}

/** Every node type offered in the editor. */
export const NODE_CATALOG: readonly CatalogEntry[] = [
  {
    op: 'ALL',
    title: 'All of',
    description: 'Every condition inside must be true.',
    category: 'Logic',
    available: always,
    create: d => ({op: 'ALL', children: [defaultLeaf(d)]}),
  },
  {
    op: 'ANY',
    title: 'Any of',
    description: 'At least one condition inside must be true.',
    category: 'Logic',
    available: always,
    create: d => ({op: 'ANY', children: [defaultLeaf(d)]}),
  },
  {
    op: 'NOT',
    title: 'Not',
    description: 'The condition inside must be false.',
    category: 'Logic',
    available: always,
    create: d => ({op: 'NOT', child: defaultLeaf(d)}),
  },
  {
    op: 'WITHIN_BARS',
    title: 'Within bars',
    description: 'The condition was true on at least one recent bar.',
    category: 'Logic',
    available: always,
    create: d => ({op: 'WITHIN_BARS', bars: 3, child: defaultLeaf(d)}),
  },
  {
    op: 'PERSIST_FOR',
    title: 'Persists for',
    description: 'The condition stayed true on every recent bar.',
    category: 'Logic',
    available: always,
    create: d => ({op: 'PERSIST_FOR', bars: 3, child: defaultLeaf(d)}),
  },
  {
    op: 'STATE',
    title: 'Indicator state',
    description:
      'A published indicator value matches on one or more closed timeframes.',
    category: 'Indicator state',
    available: vocab => firstStateValue(vocab) !== null,
    create: (_d, tf, vocab) => stateNode(tf, vocab),
  },
  {
    op: 'IN_ZONE',
    title: 'Price in a zone',
    description: 'Price is inside a live zone or at a level of chosen kinds.',
    category: 'Zones and levels',
    available: vocab => any(vocab.kinds),
    create: (d, tf, vocab) => ({
      op: 'IN_ZONE',
      kinds: [firstKinds(vocab, 1)[0] ?? ''],
      side: sideFor(d),
      tf,
    }),
  },
  {
    op: 'AOI_TOUCH',
    title: 'Touched an area of interest',
    description: 'A recent bar touched an AOI confirmed before it.',
    category: 'Zones and levels',
    available: vocab => any(vocab.kinds),
    create: (d, _tf, vocab) => ({
      op: 'AOI_TOUCH',
      kinds: [firstKinds(vocab, 1)[0] ?? ''],
      side: sideFor(d),
      within_bars: 3,
      touch: 'WICK',
    }),
  },
  {
    op: 'AOI_OVERLAP',
    title: 'Overlapping areas',
    description: 'Two kinds of active AOI overlap (confluence).',
    category: 'Zones and levels',
    available: vocab => vocab.kinds.size >= 2,
    create: (d, _tf, vocab) => ({
      op: 'AOI_OVERLAP',
      kinds_a: [firstKinds(vocab, 2)[0] ?? ''],
      kinds_b: [firstKinds(vocab, 2)[1] ?? ''],
      side: sideFor(d),
    }),
  },
  {
    op: 'EVENT',
    title: 'Indicator event',
    description: 'An indicator event confirmed on a recent closed bar.',
    category: 'Events',
    available: vocab => any(vocab.events),
    create: (_d, _tf, vocab) => ({
      op: 'EVENT',
      name: firstEvents(vocab, 1)[0] ?? '',
      within_bars: 3,
    }),
  },
  {
    op: 'SEQUENCE',
    title: 'Event sequence',
    description:
      'Events in order on distinct bars; fires once on the last step.',
    category: 'Events',
    available: vocab => any(vocab.events),
    create: (_d, _tf, vocab) => sequenceNode(vocab),
  },
  {
    op: 'COMPARE',
    title: 'Compare values',
    description: 'Compare a candle, indicator or market value with another.',
    category: 'Values',
    available: always,
    create: () => ({
      op: 'COMPARE',
      left: {kind: 'market', field: 'funding_8h_pct'},
      cmp: 'lt',
      right: {kind: 'const', value: '0.1'},
    }),
  },
  {
    op: 'IN_RANGE',
    title: 'Value in range',
    description: 'A value lies between two bounds.',
    category: 'Values',
    available: always,
    create: () => ({
      op: 'IN_RANGE',
      value: {kind: 'market', field: 'spread_pct'},
      low: {kind: 'const', value: '0'},
      high: {kind: 'const', value: '0.05'},
      inclusive: true,
    }),
  },
  {
    op: 'CROSSES',
    title: 'Crosses',
    description: 'A value crossed another on the last closed bar.',
    category: 'Values',
    available: always,
    create: d => ({
      op: 'CROSSES',
      left: {kind: 'candle', field: 'close'},
      right: {kind: 'indicator', plugin_id: 'ema', field: 'ema'},
      direction: d === 'LONG' ? 'ABOVE' : 'BELOW',
    }),
  },
  {
    op: 'TIME_WINDOW',
    title: 'Time window',
    description: 'The bar starts inside local-time windows on chosen weekdays.',
    category: 'Time',
    available: always,
    create: () => ({
      op: 'TIME_WINDOW',
      time_zone: 'UTC',
      windows: [{start: '08:00', end: '16:00'}],
    }),
  },
  {
    op: 'RISK_REWARD',
    title: 'Reward to risk',
    description: 'Projected reward/risk after costs is at least a minimum.',
    category: 'Risk',
    available: always,
    create: () => ({op: 'RISK_REWARD', min_rr: '2'}),
  },
];

/**
 * Finds the catalog entry of an operator.
 * @param op Node operator.
 * @return Entry, or undefined for unknown operators.
 */
export function catalogEntry(op: string): CatalogEntry | undefined {
  return NODE_CATALOG.find(entry => entry.op === op);
}

/** Timeframes from shortest to longest. */
export const TIMEFRAME_ORDER: readonly Timeframe[] = [
  '1s',
  '5s',
  '15s',
  '30s',
  '1m',
  '3m',
  '5m',
  '15m',
  '30m',
  '1h',
  '2h',
  '4h',
  '6h',
  '12h',
  '1d',
  '1w',
  '1M',
];
