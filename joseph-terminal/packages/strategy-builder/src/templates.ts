/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Strategy templates. Long A and Short B are the reference
 * rules from the V5 specification, identical to
 * `packages/contracts/fixtures/strategy.fixture.json`.
 */

import type {Direction, StrategyDefinition} from '@joseph/contracts';

/** Template descriptor. */
export interface StrategyTemplate {
  /** Template id. */
  id: 'long-a' | 'short-b' | 'blank-long' | 'blank-short';
  /** Menu title. */
  title: string;
  /** One-sentence summary. */
  summary: string;
  /**
   * Creates a fresh copy of the definition.
   * @return Definition (deep copy, safe to edit).
   */
  create(): StrategyDefinition;
}

/** Long A reference rule. */
const LONG_A: StrategyDefinition = {
  schema_version: 2,
  strategy_id: 'discount-ote-reversal',
  name: 'Long A: HTF bull + discount OTE + AOI + sweep then seller failure',
  direction: 'LONG',
  signal_timeframe: '5m',
  universe: {
    qualification: 'QUALIFIED_ONLY',
    venues: ['binance', 'hyperliquid'],
  },
  when: {
    op: 'ALL',
    id: 'root',
    children: [
      {
        op: 'BIAS',
        id: 'htf',
        timeframes: ['4h'],
        value: 'BULLISH',
        match: 'ALL',
      },
      {
        op: 'PRICE_IN_ZONE',
        id: 'disc',
        zone: 'DISCOUNT',
        tf: '1h',
        range_group: 'A',
      },
      {op: 'PRICE_IN_ZONE', id: 'ote', zone: 'OTE', tf: '1h', range_group: 'A'},
      {
        op: 'AOI_TOUCH',
        id: 'aoi',
        kinds: ['FVG', 'OB', 'BREAKER'],
        side: 'bullish',
        within_bars: 3,
        touch: 'WICK',
      },
      {
        op: 'SEQUENCE',
        id: 'seq',
        within_bars: 4,
        steps: [{name: 'SSL_SWEEP'}, {name: 'SELLER_FAILURE'}],
      },
      {op: 'RISK_REWARD', id: 'rr', min_rr: '2.0'},
    ],
  },
  risk: {
    min_rr: '2.0',
    stop_model: 'AOI_INVALIDATION',
    target_model: 'NEAREST_OPPOSING_LIQUIDITY',
    fee_bps_estimate: '5',
  },
  notification: {
    cooldown_bars: 12,
    once_per_aoi: true,
    emit: ['ENTRY_CONFIRMED', 'ENTRY_BLOCKED'],
    channels: ['UI'],
  },
  setup: {expire_after_bars: 24},
  data_policy: {max_data_age_ms: 120000, allow_rest_poll: true},
  execution_mode: 'PAPER',
};

/** Short B reference rule. */
const SHORT_B: StrategyDefinition = {
  schema_version: 2,
  strategy_id: 'premium-ote-short',
  name: 'Short B: HTF bear + premium OTE + bearish FVG/breaker + BSL sweep then buyer failure',
  direction: 'SHORT',
  signal_timeframe: '5m',
  universe: {qualification: 'QUALIFIED_ONLY', venues: ['binance', 'bitget']},
  when: {
    op: 'ALL',
    children: [
      {
        op: 'BIAS',
        timeframes: ['1d', '4h', '1h'],
        value: 'BEARISH',
        match: 'K_OF_N',
        k: 2,
      },
      {op: 'PRICE_IN_ZONE', zone: 'PREMIUM', tf: '1h', range_group: 'R'},
      {op: 'PRICE_IN_ZONE', zone: 'OTE', tf: '1h', range_group: 'R'},
      {
        op: 'AOI_TOUCH',
        kinds: ['FVG', 'BREAKER'],
        side: 'bearish',
        within_bars: 3,
      },
      {
        op: 'SEQUENCE',
        within_bars: 4,
        steps: [{name: 'BSL_SWEEP'}, {name: 'BUYER_FAILURE'}],
      },
      {
        op: 'NOT',
        child: {
          op: 'COMPARE',
          left: {kind: 'market', field: 'oi_change_pct'},
          cmp: 'lt',
          right: {kind: 'const', value: '-5'},
        },
      },
    ],
  },
  risk: {
    min_rr: '2.0',
    stop_model: 'AOI_INVALIDATION',
    target_model: 'NEAREST_OPPOSING_LIQUIDITY',
  },
  notification: {cooldown_bars: 12, once_per_aoi: true},
  execution_mode: 'ALERT_ONLY',
};

/**
 * Creates an empty strategy that contains a single risk/reward condition.
 * @param direction Strategy direction.
 * @return Definition.
 */
export function blankStrategy(direction: Direction): StrategyDefinition {
  return {
    schema_version: 2,
    strategy_id:
      direction === 'LONG' ? 'new-long-strategy' : 'new-short-strategy',
    name: direction === 'LONG' ? 'New long strategy' : 'New short strategy',
    direction,
    signal_timeframe: '5m',
    universe: {qualification: 'QUALIFIED_ONLY', venues: ['binance']},
    when: {
      op: 'ALL',
      id: 'root',
      children: [{op: 'RISK_REWARD', id: 'n1', min_rr: '2'}],
    },
    risk: {
      min_rr: '2',
      stop_model: 'AOI_INVALIDATION',
      target_model: 'NEAREST_OPPOSING_LIQUIDITY',
    },
    notification: {cooldown_bars: 12, once_per_aoi: true},
    execution_mode: 'ALERT_ONLY',
  };
}

/**
 * Deep-copies a JSON value.
 * @param value JSON value.
 * @return Copy.
 */
function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** Available templates. */
export const STRATEGY_TEMPLATES: readonly StrategyTemplate[] = [
  {
    id: 'long-a',
    title: 'Long A: discount OTE reversal',
    summary:
      '4h bullish bias, price in the 1h discount and OTE of one range, AOI touch, SSL sweep then seller failure, 2R minimum.',
    create: () => clone(LONG_A),
  },
  {
    id: 'short-b',
    title: 'Short B: premium OTE rejection',
    summary:
      'Two of 1d/4h/1h bearish, price in the 1h premium and OTE of one range, bearish FVG or breaker touch, BSL sweep then buyer failure, OI not collapsing.',
    create: () => clone(SHORT_B),
  },
  {
    id: 'blank-long',
    title: 'Blank long',
    summary: 'Start from a single reward-to-risk condition.',
    create: () => blankStrategy('LONG'),
  },
  {
    id: 'blank-short',
    title: 'Blank short',
    summary: 'Start from a single reward-to-risk condition.',
    create: () => blankStrategy('SHORT'),
  },
];
