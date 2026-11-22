/**
 * @fileoverview Static DEMO indicator fixture.
 *
 * Records are precomputed shapes (not computed from candles in the
 * browser): each AOI is stored relative to the latest closed bar
 * (`barsAgo`) and the latest close (`lowerRatio`/`upperRatio`) and is
 * materialized at request time. TODO(integration): replace with records
 * exported by the Python engine for the `mock` venue.
 */
import type {
  AoiKind,
  AoiState,
  StructureEventName,
  ZoneSide,
} from '@joseph/contracts';

/** Relative AOI fixture entry. */
export interface RelativeAoi {
  /** Stable id suffix. */
  key: string;
  /** Producing plugin. */
  source: string;
  /** AOI kind. */
  kind: AoiKind;
  /** Side. */
  side: ZoneSide;
  /** State. */
  state: AoiState;
  /** Bars before the latest closed bar where it originates. */
  barsAgo: number;
  /** Bars after origin when it was confirmed. */
  confirmBars: number;
  /** Lower bound as a ratio of the latest close. */
  lowerRatio: number;
  /** Upper bound as a ratio of the latest close. */
  upperRatio: number;
  /** Quality score. */
  score: number | null;
  /** Dealing range id for premium/discount/OTE. */
  rangeId?: string;
}

/** Relative event fixture entry. */
export interface RelativeEvent {
  /** Stable id suffix. */
  key: string;
  /** Producing plugin. */
  source: string;
  /** Event name. */
  name: StructureEventName;
  /** Bars before the latest closed bar. */
  barsAgo: number;
  /** Reference price ratio. */
  priceRatio: number;
}

/** DEMO AOIs. */
export const DEMO_AOIS: readonly RelativeAoi[] = [
  {
    key: 'fvg-a',
    source: 'fvg_ifvg',
    kind: 'FVG',
    side: 'bullish',
    state: 'active',
    barsAgo: 46,
    confirmBars: 2,
    lowerRatio: 0.9918,
    upperRatio: 0.9942,
    score: 76,
  },
  {
    key: 'fvg-b',
    source: 'fvg_ifvg',
    kind: 'FVG',
    side: 'bearish',
    state: 'tested',
    barsAgo: 18,
    confirmBars: 2,
    lowerRatio: 1.0061,
    upperRatio: 1.0079,
    score: 58,
  },
  {
    key: 'ifvg-a',
    source: 'fvg_ifvg',
    kind: 'IFVG',
    side: 'bearish',
    state: 'active',
    barsAgo: 92,
    confirmBars: 3,
    lowerRatio: 1.0128,
    upperRatio: 1.0141,
    score: 41,
  },
  {
    key: 'ob-a',
    source: 'order_breaker_blocks',
    kind: 'OB',
    side: 'bullish',
    state: 'active',
    barsAgo: 70,
    confirmBars: 4,
    lowerRatio: 0.9846,
    upperRatio: 0.9877,
    score: 82,
  },
  {
    key: 'brk-a',
    source: 'order_breaker_blocks',
    kind: 'BREAKER',
    side: 'bearish',
    state: 'active',
    barsAgo: 120,
    confirmBars: 5,
    lowerRatio: 1.0165,
    upperRatio: 1.0192,
    score: 47,
  },
  {
    key: 'bsl-a',
    source: 'liquidity_sweeps',
    kind: 'BSL',
    side: 'bearish',
    state: 'active',
    barsAgo: 64,
    confirmBars: 3,
    lowerRatio: 1.0108,
    upperRatio: 1.0108,
    score: null,
  },
  {
    key: 'ssl-a',
    source: 'liquidity_sweeps',
    kind: 'SSL',
    side: 'bullish',
    state: 'active',
    barsAgo: 38,
    confirmBars: 3,
    lowerRatio: 0.9893,
    upperRatio: 0.9893,
    score: null,
  },
  {
    key: 'eql-a',
    source: 'liquidity_sweeps',
    kind: 'EQL',
    side: 'bullish',
    state: 'swept',
    barsAgo: 110,
    confirmBars: 3,
    lowerRatio: 0.9812,
    upperRatio: 0.9812,
    score: null,
  },
  {
    key: 'pd-prem',
    source: 'premium_discount_ote',
    kind: 'PREMIUM',
    side: 'bearish',
    state: 'active',
    barsAgo: 150,
    confirmBars: 10,
    lowerRatio: 1.0012,
    upperRatio: 1.0215,
    score: null,
    rangeId: 'range-demo-7c1e',
  },
  {
    key: 'pd-disc',
    source: 'premium_discount_ote',
    kind: 'DISCOUNT',
    side: 'bullish',
    state: 'active',
    barsAgo: 150,
    confirmBars: 10,
    lowerRatio: 0.9809,
    upperRatio: 1.0012,
    score: null,
    rangeId: 'range-demo-7c1e',
  },
  {
    key: 'pd-ote',
    source: 'premium_discount_ote',
    kind: 'OTE',
    side: 'bullish',
    state: 'active',
    barsAgo: 150,
    confirmBars: 10,
    lowerRatio: 0.9852,
    upperRatio: 0.9886,
    score: null,
    rangeId: 'range-demo-7c1e',
  },
  {
    key: 'va-vah',
    source: 'auction_value_areas',
    kind: 'VAH',
    side: 'neutral',
    state: 'active',
    barsAgo: 20,
    confirmBars: 0,
    lowerRatio: 1.0046,
    upperRatio: 1.0046,
    score: null,
  },
  {
    key: 'va-poc',
    source: 'auction_value_areas',
    kind: 'POC',
    side: 'neutral',
    state: 'active',
    barsAgo: 20,
    confirmBars: 0,
    lowerRatio: 0.9988,
    upperRatio: 0.9988,
    score: null,
  },
  {
    key: 'va-val',
    source: 'auction_value_areas',
    kind: 'VAL',
    side: 'neutral',
    state: 'active',
    barsAgo: 20,
    confirmBars: 0,
    lowerRatio: 0.9931,
    upperRatio: 0.9931,
    score: null,
  },
  {
    key: 'pdh',
    source: 'daily_bias',
    kind: 'PDH',
    side: 'neutral',
    state: 'active',
    barsAgo: 200,
    confirmBars: 0,
    lowerRatio: 1.0232,
    upperRatio: 1.0232,
    score: null,
  },
  {
    key: 'pdl',
    source: 'daily_bias',
    kind: 'PDL',
    side: 'neutral',
    state: 'active',
    barsAgo: 200,
    confirmBars: 0,
    lowerRatio: 0.9771,
    upperRatio: 0.9771,
    score: null,
  },
];

/** DEMO structure events. */
export const DEMO_EVENTS: readonly RelativeEvent[] = [
  {
    key: 'bos-1',
    source: 'daily_bias',
    name: 'BOS_BULL',
    barsAgo: 54,
    priceRatio: 1.0021,
  },
  {
    key: 'choch-1',
    source: 'daily_bias',
    name: 'CHOCH_BEAR',
    barsAgo: 132,
    priceRatio: 1.0094,
  },
  {
    key: 'sweep-1',
    source: 'liquidity_sweeps',
    name: 'SSL_SWEEP',
    barsAgo: 9,
    priceRatio: 0.9893,
  },
  {
    key: 'sf-1',
    source: 'order_flow_pressure',
    name: 'SELLER_FAILURE',
    barsAgo: 7,
    priceRatio: 0.9901,
  },
  {
    key: 'bb-1',
    source: 'order_flow_pressure',
    name: 'BUYER_BREAKOUT',
    barsAgo: 28,
    priceRatio: 1.0032,
  },
];
