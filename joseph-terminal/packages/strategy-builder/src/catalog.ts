/**
 * @fileoverview Catalog of rule nodes for the "add condition" menu, with
 * default factories and the indicator plugin each node depends on.
 */
import type {
  AoiKind,
  Direction,
  RuleNode,
  StructureEventName,
  Timeframe,
} from '@joseph/contracts';

/** Menu categories. */
export type NodeCategory =
  | 'Logic'
  | 'Structure'
  | 'Zones'
  | 'Areas of interest'
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
   * Creates a default node for the strategy direction.
   * @param direction Strategy direction.
   * @param signalTf Signal timeframe.
   * @return New node without an id.
   */
  create(direction: Direction, signalTf: Timeframe): RuleNode;
}

/**
 * Returns the zone side matching a direction.
 * @param direction Strategy direction.
 * @return `bullish` for LONG, `bearish` for SHORT.
 */
export function sideFor(direction: Direction): 'bullish' | 'bearish' {
  return direction === 'LONG' ? 'bullish' : 'bearish';
}

/** Every node type offered in the editor. */
export const NODE_CATALOG: readonly CatalogEntry[] = [
  {
    op: 'ALL',
    title: 'All of',
    description: 'Every condition inside must be true.',
    category: 'Logic',
    create: (d, tf) => ({op: 'ALL', children: [defaultLeaf(d, tf)]}),
  },
  {
    op: 'ANY',
    title: 'Any of',
    description: 'At least one condition inside must be true.',
    category: 'Logic',
    create: (d, tf) => ({op: 'ANY', children: [defaultLeaf(d, tf)]}),
  },
  {
    op: 'NOT',
    title: 'Not',
    description: 'The condition inside must be false.',
    category: 'Logic',
    create: (d, tf) => ({op: 'NOT', child: defaultLeaf(d, tf)}),
  },
  {
    op: 'WITHIN_BARS',
    title: 'Within bars',
    description: 'The condition was true on at least one recent bar.',
    category: 'Logic',
    create: (d, tf) => ({
      op: 'WITHIN_BARS',
      bars: 3,
      child: defaultLeaf(d, tf),
    }),
  },
  {
    op: 'PERSIST_FOR',
    title: 'Persists for',
    description: 'The condition stayed true on every recent bar.',
    category: 'Logic',
    create: (d, tf) => ({
      op: 'PERSIST_FOR',
      bars: 3,
      child: defaultLeaf(d, tf),
    }),
  },
  {
    op: 'BIAS',
    title: 'Higher-timeframe bias',
    description: 'Structural trend on closed higher-timeframe bars.',
    category: 'Structure',
    create: d => ({
      op: 'BIAS',
      timeframes: ['4h'],
      value: d === 'LONG' ? 'BULLISH' : 'BEARISH',
      match: 'ALL',
    }),
  },
  {
    op: 'EVENT',
    title: 'Structure event',
    description: 'A sweep, break or pressure event on a recent closed bar.',
    category: 'Structure',
    create: d => ({
      op: 'EVENT',
      name: d === 'LONG' ? 'SSL_SWEEP' : 'BSL_SWEEP',
      within_bars: 3,
    }),
  },
  {
    op: 'SEQUENCE',
    title: 'Event sequence',
    description:
      'Events in order on distinct bars; fires once on the last step.',
    category: 'Structure',
    create: d => ({
      op: 'SEQUENCE',
      within_bars: 4,
      steps:
        d === 'LONG'
          ? [{name: 'SSL_SWEEP'}, {name: 'SELLER_FAILURE'}]
          : [{name: 'BSL_SWEEP'}, {name: 'BUYER_FAILURE'}],
    }),
  },
  {
    op: 'PRICE_IN_ZONE',
    title: 'Price in zone',
    description: 'Price inside the premium, discount, equilibrium or OTE zone.',
    category: 'Zones',
    create: d => ({
      op: 'PRICE_IN_ZONE',
      zone: d === 'LONG' ? 'DISCOUNT' : 'PREMIUM',
      tf: '1h',
      range_group: 'A',
    }),
  },
  {
    op: 'AOI_TOUCH',
    title: 'Touched an area of interest',
    description: 'A recent bar touched an AOI confirmed before it.',
    category: 'Areas of interest',
    create: d => ({
      op: 'AOI_TOUCH',
      kinds: ['FVG', 'OB'],
      side: sideFor(d),
      within_bars: 3,
      touch: 'WICK',
    }),
  },
  {
    op: 'AOI_OVERLAP',
    title: 'Overlapping areas',
    description: 'Two kinds of active AOI overlap (confluence).',
    category: 'Areas of interest',
    create: d => ({
      op: 'AOI_OVERLAP',
      kinds_a: ['FVG'],
      kinds_b: ['OB'],
      side: sideFor(d),
    }),
  },
  {
    op: 'COMPARE',
    title: 'Compare values',
    description: 'Compare a candle, indicator or market value with another.',
    category: 'Values',
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
    create: d => ({
      op: 'CROSSES',
      left: {kind: 'candle', field: 'close'},
      right: {
        kind: 'indicator',
        plugin_id: 'auction_value_areas',
        field: 'poc',
      },
      direction: d === 'LONG' ? 'ABOVE' : 'BELOW',
    }),
  },
  {
    op: 'SESSION',
    title: 'Trading session',
    description: 'The bar starts inside a New York-time session window.',
    category: 'Time',
    create: () => ({op: 'SESSION', sessions: ['LONDON', 'NEW_YORK']}),
  },
  {
    op: 'RISK_REWARD',
    title: 'Reward to risk',
    description: 'Projected reward/risk after costs is at least a minimum.',
    category: 'Risk',
    create: () => ({op: 'RISK_REWARD', min_rr: '2'}),
  },
];

/**
 * Default leaf used when creating groups.
 * @param direction Strategy direction.
 * @param _signalTf Signal timeframe (unused).
 * @return A higher-timeframe bias node matching the direction.
 */
function defaultLeaf(direction: Direction, _signalTf: Timeframe): RuleNode {
  return {
    op: 'BIAS',
    timeframes: ['4h'],
    value: direction === 'LONG' ? 'BULLISH' : 'BEARISH',
    match: 'ALL',
  };
}

/**
 * Finds the catalog entry of an operator.
 * @param op Node operator.
 * @return Entry, or undefined for unknown operators.
 */
export function catalogEntry(op: string): CatalogEntry | undefined {
  return NODE_CATALOG.find(entry => entry.op === op);
}

/** Plugin that produces each AOI kind. */
export const AOI_KIND_PLUGIN: Readonly<Record<AoiKind, string>> = {
  FVG: 'fvg_ifvg',
  IFVG: 'fvg_ifvg',
  OB: 'order_breaker_blocks',
  BREAKER: 'order_breaker_blocks',
  BSL: 'liquidity_sweeps',
  SSL: 'liquidity_sweeps',
  EQH: 'liquidity_sweeps',
  EQL: 'liquidity_sweeps',
  PREMIUM: 'premium_discount_ote',
  DISCOUNT: 'premium_discount_ote',
  EQUILIBRIUM: 'premium_discount_ote',
  OTE: 'premium_discount_ote',
  VAH: 'auction_value_areas',
  POC: 'auction_value_areas',
  VAL: 'auction_value_areas',
  PDH: 'daily_bias',
  PDL: 'daily_bias',
  SESSION_HIGH: 'daily_bias',
  SESSION_LOW: 'daily_bias',
  SWING_HIGH: 'market_structure',
  SWING_LOW: 'market_structure',
};

/** Plugin that emits each structure event (null = any plugin). */
export const EVENT_PLUGIN: Readonly<Record<StructureEventName, string | null>> =
  {
    BOS_BULL: 'daily_bias',
    BOS_BEAR: 'daily_bias',
    CHOCH_BULL: 'daily_bias',
    CHOCH_BEAR: 'daily_bias',
    INITIAL_BREAK_BULL: 'daily_bias',
    INITIAL_BREAK_BEAR: 'daily_bias',
    FVG_FORMED: 'fvg_ifvg',
    IFVG_FORMED: 'fvg_ifvg',
    OB_FORMED: 'order_breaker_blocks',
    BREAKER_FORMED: 'order_breaker_blocks',
    AOI_INVALIDATED: null,
    AOI_EXPIRED: null,
    SSL_SWEEP: 'liquidity_sweeps',
    BSL_SWEEP: 'liquidity_sweeps',
    SSL_BREAK: 'liquidity_sweeps',
    BSL_BREAK: 'liquidity_sweeps',
    BUYER_FAILURE: 'order_flow_pressure',
    SELLER_FAILURE: 'order_flow_pressure',
    BUYER_BREAKOUT: 'order_flow_pressure',
    SELLER_BREAKOUT: 'order_flow_pressure',
    RANGE_RESET: 'premium_discount_ote',
    VALUE_AREA_UPDATED: 'auction_value_areas',
    SWING_HH: 'market_structure',
    SWING_HL: 'market_structure',
    SWING_LH: 'market_structure',
    SWING_LL: 'market_structure',
    SWING_EQH: 'market_structure',
    SWING_EQL: 'market_structure',
  };

/** All AOI kinds in display order. */
export const AOI_KINDS: readonly AoiKind[] = Object.keys(
  AOI_KIND_PLUGIN
) as AoiKind[];

/** All structure events in display order. */
export const STRUCTURE_EVENTS: readonly StructureEventName[] = Object.keys(
  EVENT_PLUGIN
) as StructureEventName[];

/** Timeframes from shortest to longest. */
export const TIMEFRAME_ORDER: readonly Timeframe[] = [
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
];
