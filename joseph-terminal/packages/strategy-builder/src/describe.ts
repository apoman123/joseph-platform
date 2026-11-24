/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Plain-language rendering of rule nodes for condition cards.
 *
 * Event and area-of-interest (AOI) labels keep the domain abbreviations used
 * across the editor (BOS, FVG, OB, SSL and so on).
 */

import type {
  AoiKind,
  Operand,
  RuleNode,
  StructureEventName,
  Timeframe,
} from '@joseph/contracts';

/** Human names of structure events. */
const EVENT_NAMES: Readonly<Record<StructureEventName, string>> = {
  BOS_BULL: 'bullish BOS',
  BOS_BEAR: 'bearish BOS',
  CHOCH_BULL: 'bullish CHoCH',
  CHOCH_BEAR: 'bearish CHoCH',
  INITIAL_BREAK_BULL: 'bullish initial break',
  INITIAL_BREAK_BEAR: 'bearish initial break',
  FVG_FORMED: 'FVG formed',
  IFVG_FORMED: 'IFVG formed',
  OB_FORMED: 'order block formed',
  BREAKER_FORMED: 'breaker formed',
  AOI_INVALIDATED: 'AOI invalidated',
  AOI_EXPIRED: 'AOI expired',
  SSL_SWEEP: 'SSL sweep',
  BSL_SWEEP: 'BSL sweep',
  SSL_BREAK: 'SSL break',
  BSL_BREAK: 'BSL break',
  BUYER_FAILURE: 'buyer failure',
  SELLER_FAILURE: 'seller failure',
  BUYER_BREAKOUT: 'buyer breakout',
  SELLER_BREAKOUT: 'seller breakout',
  RANGE_RESET: 'dealing range reset',
  VALUE_AREA_UPDATED: 'value area updated',
  SWING_HH: 'higher high',
  SWING_HL: 'higher low',
  SWING_LH: 'lower high',
  SWING_LL: 'lower low',
  SWING_EQH: 'equal high',
  SWING_EQL: 'equal low',
};

/** Human names of AOI kinds. */
const AOI_NAMES: Readonly<Record<AoiKind, string>> = {
  FVG: 'FVG',
  IFVG: 'IFVG',
  OB: 'OB',
  BREAKER: 'breaker',
  BSL: 'BSL',
  SSL: 'SSL',
  EQH: 'EQH',
  EQL: 'EQL',
  PREMIUM: 'premium zone',
  DISCOUNT: 'discount zone',
  EQUILIBRIUM: 'equilibrium',
  OTE: 'OTE zone',
  VAH: 'VAH',
  POC: 'POC',
  VAL: 'VAL',
  PDH: 'prior-day high',
  PDL: 'prior-day low',
  SESSION_HIGH: 'session high',
  SESSION_LOW: 'session low',
  SWING_HIGH: 'unbroken swing high',
  SWING_LOW: 'unbroken swing low',
};

/**
 * Returns the human name of a structure event.
 * @param name Event name.
 * @return Name such as `SSL sweep`.
 */
export function eventName(name: StructureEventName): string {
  return EVENT_NAMES[name];
}

/**
 * Returns the human name of an AOI kind.
 * @param kind AOI kind.
 * @return Name such as `breaker`.
 */
export function aoiName(kind: AoiKind): string {
  return AOI_NAMES[kind];
}

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

/** Human names of market operand fields. */
const MARKET_FIELDS: Readonly<Record<string, string>> = {
  funding_8h_pct: 'funding (8h %)',
  open_interest_usd: 'open interest (USD)',
  oi_change_pct: 'OI change %',
  spread_pct: 'spread %',
  turnover_24h_quote: '24h turnover',
};

/**
 * Describes an operand.
 * @param operand Constant, candle, indicator or market operand.
 * @return Text such as `close`, `close on 1h 2 bars ago`,
 *     `auction_value_areas.poc` or `funding (8h %)`; `value` for an unknown
 *     operand kind.
 */
export function describeOperand(operand: Operand): string {
  switch (operand.kind) {
    case 'const':
      return operand.value;
    case 'candle': {
      const ago = operand.bars_ago ? ` ${operand.bars_ago} bars ago` : '';
      return `${operand.field === 'base_volume' ? 'volume' : operand.field}${onTf(operand.tf)}${ago}`;
    }
    case 'indicator': {
      const ago = operand.bars_ago ? ` ${operand.bars_ago} bars ago` : '';
      return `${operand.plugin_id}.${operand.field}${onTf(operand.tf)}${ago}`;
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
 * Renders one node as a plain-language sentence (children not included).
 * @param node Rule node.
 * @return Sentence without a final period, such as `4h bias is bullish`;
 *     `Unsupported condition` for an unknown operator.
 */
export function describeNode(node: RuleNode): string {
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
    case 'BIAS': {
      const value = node.value.toLowerCase();
      const tfs = [...node.timeframes];
      const bos = node.require_bos ? ', latest break is a BOS' : '';
      if (node.match === 'K_OF_N') {
        return `At least ${node.k ?? tfs.length} of ${joinWords(tfs, 'and')} bias are ${value}${bos}`;
      }
      if (tfs.length === 1) return `${tfs[0]} bias is ${value}${bos}`;
      return `${joinWords(tfs, 'and')} bias are all ${value}${bos}`;
    }
    case 'PRICE_IN_ZONE': {
      const source = {
        close: 'Close',
        high: 'High',
        low: 'Low',
        hl2: 'Bar midpoint',
      }[node.price_source ?? 'close'];
      const zone = {
        PREMIUM: 'premium zone',
        DISCOUNT: 'discount zone',
        EQUILIBRIUM: 'equilibrium band',
        OTE: 'OTE zone',
      }[node.zone];
      const group = node.range_group
        ? ` (range group ${node.range_group})`
        : '';
      return `${source} is inside the ${node.tf} ${zone}${group}`;
    }
    case 'AOI_TOUCH': {
      const kinds = joinWords(node.kinds.map(aoiName), 'or');
      const article = /^[aeiouAEIOU]/.test(node.side) ? 'an' : 'a';
      const touch = (node.touch ?? 'WICK').toLowerCase();
      const quality = node.min_quality ? `, quality ≥ ${node.min_quality}` : '';
      const active =
        node.require_active === false ? ', including used AOIs' : '';
      return `Touched ${article} ${node.side} ${kinds}${onTf(node.tf)} ${lookback(node.within_bars)} (${touch}${quality}${active})`;
    }
    case 'AOI_OVERLAP':
      return `A ${node.side} ${joinWords(node.kinds_a.map(aoiName), 'or')} overlaps a ${joinWords(node.kinds_b.map(aoiName), 'or')}${onTf(node.tf)}`;
    case 'EVENT': {
      const name = eventName(node.name);
      return `${name.charAt(0).toUpperCase()}${name.slice(1)} confirmed${onTf(node.tf)} ${lookback(node.within_bars)}`;
    }
    case 'SEQUENCE': {
      const steps = node.steps
        .map(step => `${eventName(step.name)}${onTf(step.tf)}`)
        .join(' then ');
      return `${steps.charAt(0).toUpperCase()}${steps.slice(1)} within ${node.within_bars} bars`;
    }
    case 'COMPARE':
      return `${capital(describeOperand(node.left))} ${COMPARATORS[node.cmp]} ${describeOperand(node.right)}`;
    case 'IN_RANGE':
      return `${capital(describeOperand(node.value))} is between ${describeOperand(node.low)} and ${describeOperand(node.high)}${node.inclusive === false ? ' (exclusive)' : ''}`;
    case 'CROSSES':
      return `${capital(describeOperand(node.left))} crosses ${node.direction.toLowerCase()} ${describeOperand(node.right)}`;
    case 'SESSION':
      return `Bar starts in the ${joinWords(
        node.sessions.map(s =>
          s === 'NEW_YORK' ? 'New York' : s.charAt(0) + s.slice(1).toLowerCase()
        ),
        'or'
      )} session`;
    case 'RISK_REWARD':
      return `Projected reward to risk after costs is at least ${node.min_rr}R`;
    default:
      return 'Unsupported condition';
  }
}

/**
 * Capitalizes the first character.
 * @param text Text.
 * @return Capitalized text.
 */
function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Renders a whole tree as an indented outline.
 * @param node Root node.
 * @param depth Indentation level of `node`, two spaces per level.
 * @return One `describeNode` line per node in pre-order, joined by `\n`.
 */
export function describeTree(node: RuleNode, depth = 0): string {
  const pad = '  '.repeat(depth);
  const line = `${pad}${describeNode(node)}`;
  if (node.op === 'ALL' || node.op === 'ANY') {
    return [
      line,
      ...node.children.map(child => describeTree(child, depth + 1)),
    ].join('\n');
  }
  if (
    node.op === 'NOT' ||
    node.op === 'PERSIST_FOR' ||
    node.op === 'WITHIN_BARS'
  ) {
    return [line, describeTree(node.child, depth + 1)].join('\n');
  }
  return line;
}
