/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO indicator plugin descriptors.
 *
 * Input schemas mirror the market engine's `SettingSpec` declarations
 * (names, types, defaults and bounds) so the terminal's settings form can
 * be exercised without a backend. The DEMO fixture itself does not
 * recompute zones when inputs change; the engine does.
 */

import {
  TIMEFRAMES,
  type AoiKind,
  type IndicatorPluginInfo,
  type StructureEventName,
} from '@joseph/contracts';

/**
 * Compact setting declaration: name, type, default, description, then the
 * optional minimum and maximum (`int`, `decimal`) or the allowed values
 * (`enum`).
 */
type DemoSetting =
  | [string, 'int', number, string, number?, number?]
  | [string, 'decimal', string, string, string?, string?]
  | [string, 'enum', string, string, readonly string[]];

/** Pattern the engine uses for decimal settings. */
const DECIMAL_PATTERN = '^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$';

/** Platform-filled tick size setting shared by every plugin. */
const TICK: DemoSetting = [
  'tick_size',
  'decimal',
  '0',
  'Symbol tick size (Pine syminfo.mintick). 0 = filled from the instrument.',
];

/**
 * Builds the swing length setting (pivot bars on each side, at least 2).
 * @param defaultValue Default, as in the Pine script.
 * @param maximum Maximum, as in the Pine script (default 20).
 * @return Setting.
 */
function swing(defaultValue: number, maximum = 20): DemoSetting {
  return [
    'swing_length',
    'int',
    defaultValue,
    'Pivot bars required on each side (Pine swingLength).',
    2,
    maximum,
  ];
}

/** Structure events published by daily bias and market structure. */
const STRUCTURE_EVENTS: readonly StructureEventName[] = [
  'BOS_BULL',
  'BOS_BEAR',
  'CHOCH_BULL',
  'CHOCH_BEAR',
  'INITIAL_BREAK_BULL',
  'INITIAL_BREAK_BEAR',
];

/** One DEMO plugin. */
interface DemoPlugin {
  /** Plugin id. */
  id: string;
  /** Display name. */
  name: string;
  /** Description. */
  description: string;
  /** Input settings, in form order. */
  settings: readonly DemoSetting[];
  /** AOI (area of interest) kinds produced. */
  kinds: readonly AoiKind[];
  /** Events produced. */
  events: readonly StructureEventName[];
}

/** DEMO plugins in catalog order. */
const DEMO_PLUGINS: readonly DemoPlugin[] = [
  {
    id: 'daily_bias',
    name: 'Daily bias',
    description:
      'Confirmed BOS/CHoCH per timeframe, sessions and prior-day levels.',
    settings: [swing(3), TICK],
    kinds: ['PDH', 'PDL', 'SESSION_HIGH', 'SESSION_LOW'],
    events: STRUCTURE_EVENTS,
  },
  {
    id: 'fvg_ifvg',
    name: 'FVG and IFVG',
    description: 'Three-candle fair value gaps and their inversions.',
    settings: [
      ['min_gap_ticks', 'int', 1, 'Minimum gap in ticks.', 1],
      [
        'min_gap_atr',
        'decimal',
        '0.10',
        'Minimum gap in ATR(14) units (0 disables).',
        '0',
      ],
      ['max_zones', 'int', 30, 'Zones kept (oldest dropped).', 1],
      ['max_age_bars', 'int', 500, 'Zone lifetime in bars.', 1],
      [
        'first_test_factor',
        'decimal',
        '0.70',
        'Score multiplier on the first midpoint test.',
        '0.1',
        '1',
      ],
      TICK,
    ],
    kinds: ['FVG', 'IFVG'],
    events: ['FVG_FORMED', 'IFVG_FORMED', 'AOI_INVALIDATED'],
  },
  {
    id: 'order_breaker_blocks',
    name: 'Order and breaker blocks',
    description:
      'Order blocks behind structure breaks and their breaker conversions.',
    settings: [
      swing(3),
      ['search_bars', 'int', 12, 'Bars searched for the block candle.', 1, 50],
      [
        'min_break_body_atr',
        'decimal',
        '0.50',
        'Minimum break-candle body in ATR(14) units.',
        '0',
      ],
      ['max_zones', 'int', 30, 'Zones kept (oldest dropped).', 1],
      TICK,
    ],
    kinds: ['OB', 'BREAKER'],
    events: ['OB_FORMED', 'BREAKER_FORMED', 'AOI_INVALIDATED'],
  },
  {
    id: 'liquidity_sweeps',
    name: 'Liquidity sweeps',
    description:
      'Buy-side and sell-side liquidity pools, equal highs/lows and sweeps.',
    settings: [
      swing(3),
      ['equal_ticks', 'int', 2, 'Equal-level tolerance in ticks.', 0],
      ['sweep_ticks', 'int', 1, 'Minimum penetration in ticks.', 1],
      ['max_active', 'int', 60, 'Live pools kept.', 1],
      TICK,
    ],
    kinds: ['BSL', 'SSL', 'EQH', 'EQL'],
    events: ['SSL_SWEEP', 'BSL_SWEEP', 'SSL_BREAK', 'BSL_BREAK'],
  },
  {
    id: 'order_flow_pressure',
    name: 'Order flow pressure',
    description:
      'Volume delta (true from taker volume when the venue publishes it, otherwise a candle-direction estimate) and failure/breakout events.',
    settings: [
      [
        'delta_source',
        'enum',
        'auto',
        'auto uses taker buy/sell volume when the venue publishes it (true delta) and falls back to the candle-direction estimate; estimate always uses the estimate.',
        ['auto', 'estimate'],
      ],
      [
        'intrabar_tf',
        'enum',
        '1m',
        'Lower timeframe used for the delta estimate.',
        ['1m', '3m', '5m', '15m'],
      ],
      ['min_intrabars', 'int', 5, 'Intrabars required.', 1],
      ['lookback_bars', 'int', 20, 'Prior-range and volume lookback.', 2, 200],
      [
        'min_relative_volume',
        'decimal',
        '1.50',
        'Volume versus the prior average.',
        '0',
      ],
      [
        'min_delta_share',
        'decimal',
        '0.20',
        'Minimum |delta| share of covered volume.',
        '0',
        '1',
      ],
      TICK,
    ],
    kinds: [],
    events: [
      'BUYER_FAILURE',
      'SELLER_FAILURE',
      'BUYER_BREAKOUT',
      'SELLER_BREAKOUT',
    ],
  },
  {
    id: 'auction_value_areas',
    name: 'Auction value areas',
    description: 'Rolling VAH/POC/VAL from 1H and chart-timeframe bars.',
    settings: [
      ['hourly_bars', 'int', 24, 'Completed 1H bars profiled.', 4, 48],
      ['chart_bars', 'int', 20, 'Completed chart bars profiled.', 5, 120],
      [
        'value_area_percent',
        'int',
        70,
        'Volume share of the value area.',
        50,
        90,
      ],
      TICK,
    ],
    kinds: ['VAH', 'POC', 'VAL'],
    events: ['VALUE_AREA_UPDATED'],
  },
  {
    id: 'premium_discount_ote',
    name: 'Premium, discount and OTE',
    description: 'Dealing range, equilibrium and 62-79% optimal trade entry.',
    settings: [
      swing(10, 50),
      [
        'break_confirmation',
        'enum',
        'Close',
        'What confirms a range break: the close or the wick.',
        ['Close', 'Wick'],
      ],
      TICK,
    ],
    kinds: ['PREMIUM', 'DISCOUNT', 'EQUILIBRIUM', 'OTE'],
    events: ['RANGE_RESET'],
  },
  {
    id: 'market_structure',
    name: 'Current-timeframe structure',
    description:
      'Confirmed swing labels (HH/HL/LH/LL/EQH/EQL), BOS / MSS / initial breaks on closes, and the unbroken swing high and low.',
    settings: [swing(3), TICK],
    kinds: ['SWING_HIGH', 'SWING_LOW'],
    events: [
      ...STRUCTURE_EVENTS,
      'SWING_HH',
      'SWING_HL',
      'SWING_LH',
      'SWING_LL',
      'SWING_EQH',
      'SWING_EQL',
    ],
  },
];

/**
 * Converts one compact setting to its JSON Schema property.
 * @param setting Compact setting.
 * @return JSON Schema fragment.
 */
function settingSchema(setting: DemoSetting): Record<string, unknown> {
  const [, type, defaultValue, description] = setting;
  if (type === 'enum') {
    return {
      description,
      type: 'string',
      enum: [...setting[4]],
      default: defaultValue,
    };
  }
  if (type === 'int') {
    return {
      description,
      type: 'integer',
      default: defaultValue,
      ...(setting[4] !== undefined ? {minimum: setting[4]} : {}),
      ...(setting[5] !== undefined ? {maximum: setting[5]} : {}),
    };
  }
  return {
    description,
    type: 'string',
    pattern: DECIMAL_PATTERN,
    default: defaultValue,
    ...(setting[4] !== undefined ? {minimum: setting[4]} : {}),
    ...(setting[5] !== undefined ? {maximum: setting[5]} : {}),
  };
}

/**
 * Builds the DEMO plugin descriptors.
 * @return One descriptor per plugin, in catalog order: inputs as a JSON
 *     Schema object, every timeframe supported, 50 warm-up bars.
 */
export function demoPluginInfos(): IndicatorPluginInfo[] {
  return DEMO_PLUGINS.map(plugin => ({
    plugin_id: plugin.id,
    indicator_version: '1.0.0',
    schema_version: 1,
    display_name: plugin.name,
    description: plugin.description,
    timeframes: [...TIMEFRAMES],
    warmup_bars: 50,
    required_streams: ['candles:chart'],
    input_schema: {
      $schema: 'https://json-schema.org/draft/2020-12/schema',
      type: 'object',
      additionalProperties: false,
      properties: Object.fromEntries(
        plugin.settings.map(setting => [setting[0], settingSchema(setting)])
      ),
    },
    output_kinds: [...plugin.kinds],
    output_events: [...plugin.events],
    licensing_status: 'CLEAN_ROOM_FROM_SPEC',
    parity_status: 'SPEC_ONLY',
  }));
}
