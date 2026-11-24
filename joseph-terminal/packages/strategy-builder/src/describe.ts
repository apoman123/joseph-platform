/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Plain-language rendering of rule nodes for condition cards.
 *
 * Area-of-interest (AOI) kinds, events and indicators are named with the
 * labels their plugins publish; names no installed plugin labels are shown
 * raw.
 */

import type {
  Operand,
  RiskModel,
  RuleNode,
  StateNode,
  TimeWindowNode,
  Timeframe,
} from '@joseph/contracts';
import {
  EMPTY_VOCABULARY,
  eventLabel,
  kindLabel,
  pluginName,
  type Vocabulary,
} from './vocabulary';

/**
 * Joins words as `a, b or c` / `a, b and c`.
 * @param items Words.
 * @param conjunction `or` or `and`.
 * @return Joined text.
 */
export function joinWords(
  items: readonly string[],
  conjunction: 'or' | 'and'
): string {
  if (items.length <= 1) return items[0] ?? '';
  return `${items.slice(0, -1).join(', ')} ${conjunction} ${items[items.length - 1]}`;
}

/**
 * Describes a lookback window.
 * @param bars Bars back (0 = evaluated bar only).
 * @return Text such as `on this bar` or `in the last 3 bars`.
 */
function lookback(bars: number): string {
  if (bars === 0) return 'on this bar';
  if (bars === 1) return 'in the last bar';
  return `in the last ${bars} bars`;
}

/**
 * Describes an optional timeframe suffix.
 * @param tf Timeframe, or undefined for the signal timeframe.
 * @return Text such as ` on 15m`, or empty for the signal timeframe.
 */
function onTf(tf: Timeframe | undefined): string {
  return tf ? ` on ${tf}` : '';
}

/**
 * Picks the indefinite article for a word.
 * @param word Following word.
 * @return `an` before a vowel, otherwise `a`.
 */
function article(word: string): string {
  return /^[aeiouAEIOU]/.test(word) ? 'an' : 'a';
}

/**
 * Capitalizes the first character.
 * @param text Text.
 * @return Capitalized text.
 */
function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Human names of market operand fields. */
const MARKET_FIELDS: Readonly<Record<string, string>> = {
  funding_8h_pct: 'funding (8h %)',
  open_interest_usd: 'open interest (USD)',
  oi_change_pct: 'OI change %',
  spread_pct: 'spread %',
  turnover_24h_quote: '24h turnover',
};

/** Short weekday names, ISO order (index 0 = Monday). */
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** Names of the IN_ZONE price sources. */
const PRICE_SOURCES: Readonly<Record<string, string>> = {
  close: 'Close',
  high: 'High',
  low: 'Low',
  hl2: 'Bar midpoint',
};

/**
 * Describes an operand.
 * @param operand Constant, candle, indicator or market operand.
 * @param vocab Vocabulary naming the indicators.
 * @return Text such as `close`, `close on 1h 2 bars ago`, `RSI` (a value
 *     keyed like its plugin), `Bollinger Bands upper`, `ema.ema` (plugin not
 *     installed) or `funding (8h %)`; `value` for an unknown operand kind.
 */
export function describeOperand(
  operand: Operand,
  vocab: Vocabulary = EMPTY_VOCABULARY
): string {
  switch (operand.kind) {
    case 'const':
      return operand.value;
    case 'candle': {
      const ago = operand.bars_ago ? ` ${operand.bars_ago} bars ago` : '';
      return `${operand.field === 'base_volume' ? 'volume' : operand.field}${onTf(operand.tf)}${ago}`;
    }
    case 'indicator': {
      const ago = operand.bars_ago ? ` ${operand.bars_ago} bars ago` : '';
      const info = vocab.plugins.get(operand.plugin_id);
      let name = `${operand.plugin_id}.${operand.field}`;
      if (info) {
        name =
          operand.field === operand.plugin_id
            ? info.display_name
            : `${info.display_name} ${operand.field}`;
      }
      return `${name}${onTf(operand.tf)}${ago}`;
    }
    case 'market':
      return MARKET_FIELDS[operand.field] ?? operand.field;
    default:
      return 'value';
  }
}

/** Comparison words. */
const COMPARATORS: Readonly<Record<string, string>> = {
  gt: 'is greater than',
  gte: 'is at least',
  lt: 'is less than',
  lte: 'is at most',
  eq: 'equals',
  neq: 'does not equal',
};

/**
 * Describes a STATE condition.
 * @param node STATE node.
 * @param vocab Vocabulary naming the plugin.
 * @return Text such as `Volume delta: method is taker_volume on 5m`.
 */
function describeState(node: StateNode, vocab: Vocabulary): string {
  const clauses = node.where
    .map(
      clause =>
        `${clause.field} is ${joinWords(clause.any_of.map(String), 'or')}`
    )
    .join(' and ');
  const text = `${pluginName(vocab, node.plugin_id)}: ${clauses}`;
  const tfs = [...node.timeframes];
  if (node.match === 'K_OF_N') {
    return `At least ${node.k ?? tfs.length} of ${joinWords(tfs, 'and')}: ${text}`;
  }
  return `${text} on ${joinWords(tfs, 'and')}`;
}

/**
 * Describes weekdays, collapsing runs of three or more days.
 * @param days ISO weekdays (1 = Monday).
 * @return Text such as `Mon–Fri` or `Sat, Sun`.
 */
function describeWeekdays(days: readonly number[]): string {
  const sorted = [...new Set(days)].sort((a, b) => a - b);
  const parts: string[] = [];
  let i = 0;
  while (i < sorted.length) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === (sorted[j] ?? 0) + 1) {
      j += 1;
    }
    const first = WEEKDAYS[(sorted[i] ?? 1) - 1] ?? '?';
    const last = WEEKDAYS[(sorted[j] ?? 1) - 1] ?? '?';
    if (j - i >= 2) parts.push(`${first}–${last}`);
    else parts.push(...sorted.slice(i, j + 1).map(d => WEEKDAYS[d - 1] ?? '?'));
    i = j + 1;
  }
  return parts.join(', ');
}

/**
 * Describes a TIME_WINDOW condition.
 * @param node TIME_WINDOW node.
 * @return Text such as
 *     `Bar starts between 09:30 and 11:30 (America/New_York), Mon–Fri`.
 */
function describeTimeWindow(node: TimeWindowNode): string {
  const windows = node.windows
    .map(w => `between ${w.start} and ${w.end}`)
    .join(' or ');
  const days =
    node.weekdays && new Set(node.weekdays).size < 7
      ? `, ${describeWeekdays(node.weekdays)}`
      : '';
  return `Bar starts ${windows} (${node.time_zone})${days}`;
}

/**
 * Describes an event reference.
 * @param name Event name.
 * @param source Publishing plugin, or undefined for any publisher.
 * @param tf Timeframe, or undefined for the signal timeframe.
 * @param vocab Vocabulary naming the event and the plugin.
 * @return Text such as `Break up on 15m (from Example zones)`.
 */
function describeEvent(
  name: string,
  source: string | undefined,
  tf: Timeframe | undefined,
  vocab: Vocabulary
): string {
  const from = source ? ` (from ${pluginName(vocab, source)})` : '';
  return `${eventLabel(vocab, name)}${onTf(tf)}${from}`;
}

/**
 * Renders one node as a plain-language sentence (children not included).
 * @param node Rule node.
 * @param vocab Vocabulary naming kinds, events and indicators.
 * @return Sentence without a final period, such as
 *     `Close crosses above EMA`; `Unsupported condition` for an unknown
 *     operator.
 */
export function describeNode(
  node: RuleNode,
  vocab: Vocabulary = EMPTY_VOCABULARY
): string {
  const kinds = (list: readonly string[]) =>
    joinWords(
      list.map(kind => kindLabel(vocab, kind)),
      'or'
    );
  switch (node.op) {
    case 'ALL':
      return 'All of these are true';
    case 'ANY':
      return 'Any of these is true';
    case 'NOT':
      return 'This is not true';
    case 'PERSIST_FOR':
      return `True on each of the last ${node.bars} bars`;
    case 'WITHIN_BARS':
      return `True at least once ${lookback(node.bars)}`;
    case 'STATE':
      return describeState(node, vocab);
    case 'IN_ZONE': {
      const source = PRICE_SOURCES[node.price_source ?? 'close'] ?? 'Close';
      const zone = node.side
        ? `${node.side} ${kinds(node.kinds)}`
        : kinds(node.kinds);
      const group = node.group ? ` (group ${node.group})` : '';
      return `${source} is inside ${article(zone)} ${zone} on ${node.tf}${group}`;
    }
    case 'AOI_TOUCH': {
      const touch = (node.touch ?? 'WICK').toLowerCase();
      const quality = node.min_quality ? `, quality ≥ ${node.min_quality}` : '';
      const active =
        node.require_active === false ? ', including used AOIs' : '';
      return `Touched ${article(node.side)} ${node.side} ${kinds(node.kinds)}${onTf(node.tf)} ${lookback(node.within_bars)} (${touch}${quality}${active})`;
    }
    case 'AOI_OVERLAP':
      return `A ${node.side} ${kinds(node.kinds_a)} overlaps ${article(kinds(node.kinds_b))} ${kinds(node.kinds_b)}${onTf(node.tf)}`;
    case 'EVENT': {
      const from = node.source
        ? ` (from ${pluginName(vocab, node.source)})`
        : '';
      return `${capital(eventLabel(vocab, node.name))} confirmed${onTf(node.tf)} ${lookback(node.within_bars)}${from}`;
    }
    case 'SEQUENCE': {
      const steps = node.steps
        .map(step => describeEvent(step.name, step.source, step.tf, vocab))
        .join(' then ');
      return `${capital(steps)} within ${node.within_bars} bars`;
    }
    case 'COMPARE':
      return `${capital(describeOperand(node.left, vocab))} ${COMPARATORS[node.cmp]} ${describeOperand(node.right, vocab)}`;
    case 'IN_RANGE':
      return `${capital(describeOperand(node.value, vocab))} is between ${describeOperand(node.low, vocab)} and ${describeOperand(node.high, vocab)}${node.inclusive === false ? ' (exclusive)' : ''}`;
    case 'CROSSES':
      return `${capital(describeOperand(node.left, vocab))} crosses ${node.direction.toLowerCase()} ${describeOperand(node.right, vocab)}`;
    case 'TIME_WINDOW':
      return describeTimeWindow(node);
    case 'RISK_REWARD':
      return `Projected reward to risk after costs is at least ${node.min_rr}R`;
    default:
      return 'Unsupported condition';
  }
}

/**
 * Describes the profit target of a risk model.
 * @param risk Risk model.
 * @param vocab Vocabulary naming the target kinds.
 * @return Text such as `Nearest P or R1 beyond the entry` or
 *     `Fixed 2R target`.
 */
export function describeTarget(
  risk: RiskModel,
  vocab: Vocabulary = EMPTY_VOCABULARY
): string {
  if (risk.target_model === 'FIXED_R') {
    return `Fixed ${risk.fixed_r ?? '?'}R target`;
  }
  const labels = (risk.target_kinds ?? []).map(kind => kindLabel(vocab, kind));
  return labels.length > 0
    ? `Nearest ${joinWords(labels, 'or')} beyond the entry`
    : 'Nearest zone beyond the entry (choose the zone kinds)';
}

/**
 * Renders a whole tree as an indented outline.
 * @param node Root node.
 * @param vocab Vocabulary naming kinds, events and indicators.
 * @param depth Indentation level of `node`, two spaces per level.
 * @return One `describeNode` line per node in pre-order, joined by `\n`.
 */
export function describeTree(
  node: RuleNode,
  vocab: Vocabulary = EMPTY_VOCABULARY,
  depth = 0
): string {
  const pad = '  '.repeat(depth);
  const line = `${pad}${describeNode(node, vocab)}`;
  if (node.op === 'ALL' || node.op === 'ANY') {
    return [
      line,
      ...node.children.map(child => describeTree(child, vocab, depth + 1)),
    ].join('\n');
  }
  if (
    node.op === 'NOT' ||
    node.op === 'PERSIST_FOR' ||
    node.op === 'WITHIN_BARS'
  ) {
    return [line, describeTree(node.child, vocab, depth + 1)].join('\n');
  }
  return line;
}
