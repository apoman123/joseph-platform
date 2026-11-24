/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO indicator plugin descriptors: mirrors of the market
 * engine's nine built-in plugins, in the engine's load order.
 *
 * Each descriptor repeats what the engine publishes at
 * `/v1/indicators/plugins`: the input schema (names, types, defaults and
 * bounds of its `SettingSpec`s), the published values and the display hints
 * the terminal renders from. A test compares them with the engine's export
 * (`contracts/openapi/builtin-indicator-plugins.json`). The DEMO data does
 * not recompute levels when inputs change; the DEMO studies honour the
 * length, source and band inputs.
 */

import {
  TIMEFRAMES,
  type DisplayTone,
  type IndicatorPluginInfo,
  type KindStyle,
  type LineStyle,
  type PaneLevel,
  type PaneStyle,
  type PlotStyle,
  type Timeframe,
  type ValueSpec,
} from '@joseph/contracts';

/** Pattern the engine publishes for decimal settings. */
const DECIMAL_PATTERN = '^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$';

/** JSON Schema fragment of one setting. */
type SettingSchema = Record<string, unknown>;

/** Price sources of the moving averages and oscillators. */
const ALL_SOURCES: readonly string[] = [
  'close',
  'open',
  'high',
  'low',
  'hl2',
  'hlc3',
  'ohlc4',
];

/** Price sources of the volume-weighted and regression studies. */
const AVERAGE_SOURCES: readonly string[] = ['close', 'hl2', 'hlc3', 'ohlc4'];

/** Latest accepted anchor time (2100-01-01 UTC), as in the engine. */
const MAX_ANCHOR_MS = 4_102_444_800_000;

/** Timeframes the pivot levels support (a period above the chart bars). */
const PIVOT_TIMEFRAMES: readonly Timeframe[] = [
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

/**
 * Builds an integer setting.
 * @param description Help text.
 * @param defaultValue Default.
 * @param minimum Inclusive minimum.
 * @param maximum Inclusive maximum.
 * @return JSON Schema fragment.
 */
function intSetting(
  description: string,
  defaultValue: number,
  minimum: number,
  maximum: number
): SettingSchema {
  return {
    description,
    type: 'integer',
    default: defaultValue,
    minimum,
    maximum,
  };
}

/**
 * Builds a decimal setting (a decimal string on the wire).
 * @param description Help text.
 * @param defaultValue Default, formatted like the engine (`0.8`, `2`).
 * @param bounds Optional inclusive bounds as decimal strings.
 * @return JSON Schema fragment.
 */
function decimalSetting(
  description: string,
  defaultValue: string,
  bounds: {minimum?: string; maximum?: string} = {}
): SettingSchema {
  return {
    description,
    type: 'string',
    pattern: DECIMAL_PATTERN,
    default: defaultValue,
    ...bounds,
  };
}

/**
 * Builds a choice setting.
 * @param description Help text.
 * @param choices Allowed values.
 * @param defaultValue Default.
 * @return JSON Schema fragment.
 */
function enumSetting(
  description: string,
  choices: readonly string[],
  defaultValue: string
): SettingSchema {
  return {
    description,
    type: 'string',
    enum: [...choices],
    default: defaultValue,
  };
}

/** Bar count of a moving average or oscillator. */
const LENGTH_HELP = 'Number of bars.';

/** Price source of a moving average or oscillator. */
const SOURCE: SettingSchema = enumSetting(
  'Price the study is computed from.',
  ALL_SOURCES,
  'close'
);

/** Platform-filled tick size shared by every plugin that publishes prices. */
const TICK: SettingSchema = decimalSetting(
  "Symbol tick size. 0 = filled from the instrument, else inferred from the first bar's price precision.",
  '0',
  {minimum: '0'}
);

/**
 * Wraps settings in the published input schema object.
 * @param properties Settings by name, in form order.
 * @return JSON Schema of the inputs.
 */
function inputSchema(
  properties: Readonly<Record<string, SettingSchema>>
): Record<string, unknown> {
  return {
    $schema: 'https://json-schema.org/draft/2020-12/schema',
    type: 'object',
    additionalProperties: false,
    properties,
  };
}

/**
 * Declares a published decimal value.
 * @param key Value key.
 * @param unit Display unit.
 * @param description Meaning.
 * @return Value spec.
 */
function decimalValue(
  key: string,
  unit: ValueSpec['unit'],
  description: string
): ValueSpec {
  return {key, type: 'decimal', unit, description};
}

/**
 * Declares a plotted value with every optional style field set.
 * @param value Plotted value key.
 * @param label Legend label.
 * @param pane `price` or a declared pane id.
 * @param style Render, tone and line style.
 * @return Plot style.
 */
function plot(
  value: string,
  label: string,
  pane: string,
  style: {
    render?: PlotStyle['render'];
    tone?: DisplayTone;
    lineStyle?: LineStyle;
  } = {}
): PlotStyle {
  return {
    value,
    label,
    pane,
    render: style.render ?? 'line',
    tone: style.tone ?? 'accent',
    color: null,
    line_style: style.lineStyle ?? 'solid',
    line_width: 1,
  };
}

/**
 * Declares a level kind drawn dashed, with every optional style field set.
 * @param kind AOI kind.
 * @param label Chart label.
 * @param tone Colour role.
 * @return Kind style.
 */
function levelKind(kind: string, label: string, tone: DisplayTone): KindStyle {
  return {
    kind,
    label,
    render: 'level',
    tone,
    color: null,
    fill_opacity: null,
    line_style: 'dashed',
    midline: false,
    pending_until_ended: false,
  };
}

/**
 * Declares a muted dotted reference line of a study pane.
 * @param value Level value.
 * @return Pane level.
 */
function paneLevel(value: number): PaneLevel {
  return {value, label: String(value), tone: 'muted', line_style: 'dotted'};
}

/** What differs between the DEMO descriptors. */
interface DemoPlugin {
  /** Plugin id. */
  id: string;
  /** Algorithm version. */
  version: string;
  /** Display name. */
  name: string;
  /** Description. */
  description: string;
  /** Supported timeframes. */
  timeframes: readonly Timeframe[];
  /** Warm-up bars for the default settings. */
  warmup: number;
  /** Required data streams, sorted. */
  streams: readonly string[];
  /** Input settings by name, in form order. */
  settings: Readonly<Record<string, SettingSchema>>;
  /** AOI kinds published. */
  kinds: readonly string[];
  /** Published values. */
  values: readonly ValueSpec[];
  /** Display hints. */
  display: IndicatorPluginInfo['display'];
}

/**
 * Builds the display hints of a plugin; omitted parts are empty.
 * @param base Layer flag, default visibility and paint order.
 * @param parts Kind, plot and pane styles.
 * @return Display hints with every field set.
 */
function display(
  base: {layer: boolean; visible: boolean; z: number},
  parts: {
    kinds?: readonly KindStyle[];
    plots?: readonly PlotStyle[];
    panes?: readonly PaneStyle[];
  } = {}
): IndicatorPluginInfo['display'] {
  return {
    layer: base.layer,
    default_visible: base.visible,
    z_index: base.z,
    show_events: true,
    kinds: [...(parts.kinds ?? [])],
    events: [],
    plots: [...(parts.plots ?? [])],
    panes: [...(parts.panes ?? [])],
    end_reasons: [],
  };
}

/** The anchored-VWAP band values, in publication order. */
const BAND_KEYS: readonly string[] = [
  'upper_1',
  'lower_1',
  'upper_2',
  'lower_2',
  'upper_3',
  'lower_3',
];

/**
 * Returns the legend label of a band value key.
 * @param key `upper_<k>` or `lower_<k>`.
 * @return Label such as `Upper 1`.
 */
function bandLabel(key: string): string {
  const [side, index] = key.split('_');
  return `${side === 'upper' ? 'Upper' : 'Lower'} ${index ?? ''}`;
}

/** Description of the anchored studies' `anchor_in_window` value. */
const ANCHOR_IN_WINDOW =
  'False when the anchor bar was not computed (every value is then null).';

/** The nine built-ins in the engine's load order. */
const DEMO_PLUGINS: readonly DemoPlugin[] = [
  {
    id: 'sma',
    version: '1.0.0',
    name: 'SMA',
    description: 'Simple moving average of the source price.',
    timeframes: TIMEFRAMES,
    warmup: 20,
    streams: ['candles:chart'],
    settings: {
      length: intSetting(LENGTH_HELP, 20, 1, 5000),
      source: SOURCE,
      tick_size: TICK,
    },
    kinds: [],
    values: [decimalValue('sma', 'price', 'Simple moving average.')],
    display: display(
      {layer: true, visible: false, z: 40},
      {plots: [plot('sma', 'SMA', 'price')]}
    ),
  },
  {
    id: 'ema',
    version: '1.0.0',
    name: 'EMA',
    description:
      'Exponential moving average of the source price, alpha = 2 / (length + 1), seeded with the first source value.',
    timeframes: TIMEFRAMES,
    warmup: 20,
    streams: ['candles:chart'],
    settings: {
      length: intSetting(LENGTH_HELP, 20, 1, 5000),
      source: SOURCE,
      tick_size: TICK,
    },
    kinds: [],
    values: [decimalValue('ema', 'price', 'Exponential moving average.')],
    display: display(
      {layer: true, visible: true, z: 45},
      {plots: [plot('ema', 'EMA', 'price')]}
    ),
  },
  {
    id: 'anchored_vwap',
    version: '2.0.0',
    name: 'VWAP',
    description:
      'Volume-weighted average price from a session, week, month or a chosen bar, with up to three standard-deviation bands.',
    timeframes: TIMEFRAMES,
    warmup: 1,
    streams: ['candles:chart'],
    settings: {
      anchor: enumSetting(
        'Reset period (session or UTC day, week, month) or a fixed anchor bar (time).',
        ['session', 'week', 'month', 'time'],
        'session'
      ),
      anchor_ms: intSetting(
        'Anchor time (UTC ms) when anchor is time.',
        0,
        0,
        MAX_ANCHOR_MS
      ),
      source: enumSetting(
        'Price the study is computed from.',
        AVERAGE_SOURCES,
        'hlc3'
      ),
      band_1: decimalSetting(
        'Band 1 width in standard deviations (0 hides it).',
        '1',
        {minimum: '0', maximum: '10'}
      ),
      band_2: decimalSetting(
        'Band 2 width in standard deviations (0 hides it).',
        '0',
        {minimum: '0', maximum: '10'}
      ),
      band_3: decimalSetting(
        'Band 3 width in standard deviations (0 hides it).',
        '0',
        {minimum: '0', maximum: '10'}
      ),
      tick_size: TICK,
    },
    kinds: [],
    values: [
      decimalValue(
        'vwap',
        'price',
        'Volume-weighted average price since the anchor.'
      ),
      decimalValue(
        'stdev',
        'price',
        'Volume-weighted standard deviation of the source.'
      ),
      ...BAND_KEYS.map(key =>
        decimalValue(
          key,
          'price',
          `${bandLabel(key)} band (null when its multiplier is 0).`
        )
      ),
      {
        key: 'anchor_ms',
        type: 'timestamp_ms',
        unit: 'none',
        description: 'Open time of the bar the average started at.',
      },
      {
        key: 'bars',
        type: 'integer',
        unit: 'bars',
        description: 'Bars since the anchor bar, counting the current one.',
      },
      {
        key: 'anchor_in_window',
        type: 'boolean',
        unit: 'none',
        description: ANCHOR_IN_WINDOW,
      },
    ],
    display: display(
      {layer: true, visible: false, z: 50},
      {
        plots: [
          plot('vwap', 'VWAP', 'price'),
          ...BAND_KEYS.map(key =>
            plot(key, bandLabel(key), 'price', {
              tone: 'neutral',
              lineStyle: 'dashed',
            })
          ),
        ],
      }
    ),
  },
  {
    id: 'regression_channel',
    version: '2.0.0',
    name: 'Regression channel',
    description:
      "Least-squares trend line with standard-deviation bands and Pearson's R, rolling or from an anchor bar.",
    timeframes: TIMEFRAMES,
    warmup: 100,
    streams: ['candles:chart'],
    settings: {
      length: intSetting(
        'Bars in the rolling window (ignored with an anchor).',
        100,
        2,
        5000
      ),
      anchor_ms: intSetting(
        'Anchor time (UTC ms); 0 uses the rolling window.',
        0,
        0,
        MAX_ANCHOR_MS
      ),
      source: enumSetting(
        'Price the study is computed from.',
        AVERAGE_SOURCES,
        'close'
      ),
      deviations: decimalSetting('Band width in standard deviations.', '2', {
        minimum: '0',
        maximum: '10',
      }),
      tick_size: TICK,
    },
    kinds: [],
    values: [
      {
        key: 'start_ms',
        type: 'timestamp_ms',
        unit: 'none',
        description: 'Open time of the oldest bar of the fit.',
      },
      decimalValue('start', 'price', 'Line value at the oldest bar.'),
      decimalValue('end', 'price', 'Line value at the current bar.'),
      decimalValue('upper_start', 'price', 'Upper band at the oldest bar.'),
      decimalValue('upper_end', 'price', 'Upper band at the current bar.'),
      decimalValue('lower_start', 'price', 'Lower band at the oldest bar.'),
      decimalValue('lower_end', 'price', 'Lower band at the current bar.'),
      decimalValue('slope', 'price', 'Slope of the line, price per bar.'),
      decimalValue(
        'stdev',
        'price',
        'Sample standard deviation of the residuals.'
      ),
      decimalValue(
        'pearson_r',
        'ratio',
        "Pearson's correlation of price and bar index."
      ),
      decimalValue('max_up', 'price', 'Largest high above the line.'),
      decimalValue('max_down', 'price', 'Largest low below the line.'),
      {
        key: 'bars',
        type: 'integer',
        unit: 'bars',
        description: 'Bars in the fit.',
      },
      {
        key: 'anchor_in_window',
        type: 'boolean',
        unit: 'none',
        description: ANCHOR_IN_WINDOW,
      },
    ],
    display: display({layer: false, visible: false, z: 50}),
  },
  {
    id: 'bollinger_bands',
    version: '1.0.0',
    name: 'Bollinger Bands',
    description:
      'Simple moving average of the source price with bands at a multiple of the population standard deviation.',
    timeframes: TIMEFRAMES,
    warmup: 20,
    streams: ['candles:chart'],
    settings: {
      length: intSetting(LENGTH_HELP, 20, 1, 5000),
      source: SOURCE,
      mult: decimalSetting(
        'Band distance from the basis in standard deviations.',
        '2',
        {minimum: '0.001', maximum: '50'}
      ),
      tick_size: TICK,
    },
    kinds: [],
    values: [
      decimalValue(
        'basis',
        'price',
        'Simple moving average of the source price.'
      ),
      decimalValue('upper', 'price', 'Basis plus mult standard deviations.'),
      decimalValue('lower', 'price', 'Basis minus mult standard deviations.'),
      decimalValue(
        'bandwidth',
        'percent',
        'Band width as a percentage of the basis; null when the basis is 0.'
      ),
      decimalValue(
        'percent_b',
        'ratio',
        'Position of the source price between the bands (0 at the lower band, 1 at the upper band); null when the bands coincide.'
      ),
    ],
    display: display(
      {layer: true, visible: false, z: 30},
      {
        plots: [
          plot('basis', 'Basis', 'price'),
          plot('upper', 'Upper', 'price', {tone: 'neutral'}),
          plot('lower', 'Lower', 'price', {tone: 'neutral'}),
        ],
      }
    ),
  },
  {
    id: 'rsi',
    version: '1.0.0',
    name: 'RSI',
    description:
      'Relative strength index from Wilder moving averages of gains and losses, seeded with their simple average; 100 when there are no losses, 0 when there are no gains.',
    timeframes: TIMEFRAMES,
    warmup: 15,
    streams: ['candles:chart'],
    settings: {length: intSetting(LENGTH_HELP, 14, 1, 500), source: SOURCE},
    kinds: [],
    values: [decimalValue('rsi', 'none', 'Relative strength index, 0 to 100.')],
    display: display(
      {layer: true, visible: false, z: 50},
      {
        plots: [plot('rsi', 'RSI', 'rsi')],
        panes: [
          {
            id: 'rsi',
            title: 'RSI',
            precision: 2,
            levels: [paneLevel(70), paneLevel(50), paneLevel(30)],
          },
        ],
      }
    ),
  },
  {
    id: 'atr',
    version: '1.0.0',
    name: 'ATR',
    description:
      'Average true range: Wilder moving average of the true range, seeded with its simple average.',
    timeframes: TIMEFRAMES,
    warmup: 14,
    streams: ['candles:chart'],
    settings: {length: intSetting(LENGTH_HELP, 14, 1, 500), tick_size: TICK},
    kinds: [],
    values: [decimalValue('atr', 'price', 'Average true range.')],
    display: display(
      {layer: true, visible: false, z: 50},
      {
        plots: [plot('atr', 'ATR', 'atr')],
        panes: [{id: 'atr', title: 'ATR', precision: 4, levels: []}],
      }
    ),
  },
  {
    id: 'volume_delta',
    version: '1.0.0',
    name: 'Volume delta',
    description:
      'Buy minus sell volume of each bar as a percentage of its volume: venue taker volume or classified trades when available, otherwise an estimate from intrabar candle direction.',
    timeframes: TIMEFRAMES,
    warmup: 1,
    streams: ['candles:chart', 'intrabars:1m'],
    settings: {
      delta_source: enumSetting(
        'auto: true delta from venue taker volume when available, else the estimate; estimate: always the intrabar estimate.',
        ['auto', 'estimate'],
        'auto'
      ),
      intrabar_tf: enumSetting(
        'Lower timeframe used for the estimate.',
        ['auto', '1s', '5s', '15s', '1m', '3m', '5m', '15m'],
        'auto'
      ),
      min_intrabars: intSetting('Intrabars required.', 5, 1, 100),
      min_coverage: decimalSetting(
        'Share of chart volume the intrabars must cover.',
        '0.8',
        {minimum: '0.5', maximum: '1'}
      ),
    },
    kinds: [],
    values: [
      decimalValue(
        'delta_pct',
        'percent',
        'Delta as a percentage of the covered volume.'
      ),
      decimalValue('delta', 'volume', 'Buy volume minus sell volume.'),
      decimalValue('buy_volume', 'volume', 'Buy volume.'),
      decimalValue('sell_volume', 'volume', 'Sell volume.'),
      {
        key: 'method',
        type: 'string',
        unit: 'none',
        description: 'How buy and sell volume were obtained.',
        choices: ['taker_volume', 'trade_classification', 'candle_direction'],
      },
      decimalValue(
        'coverage_pct',
        'percent',
        'Share of the bar volume the method covered.'
      ),
      {
        key: 'intrabar_count',
        type: 'integer',
        unit: 'none',
        description: 'Intrabars used by the estimate.',
      },
    ],
    display: display(
      {layer: true, visible: true, z: 70},
      {
        plots: [
          plot('delta_pct', 'Delta %', 'delta', {
            render: 'histogram',
            tone: 'sign',
          }),
        ],
        panes: [
          {id: 'delta', title: 'Delta %', precision: 2, levels: [paneLevel(0)]},
        ],
      }
    ),
  },
  {
    id: 'pivot_points',
    version: '1.0.0',
    name: 'Pivot points',
    description:
      "Classic floor pivot levels P, R1 to R3 and S1 to S3 from the previous period's high, low and close.",
    timeframes: PIVOT_TIMEFRAMES,
    warmup: 1,
    streams: ['aux:1d', 'candles:chart'],
    settings: {
      period: enumSetting(
        'Period whose high, low and close set the levels.',
        ['1d', '1w', '1M'],
        '1d'
      ),
      tick_size: TICK,
    },
    kinds: [
      'PIVOT_P',
      'PIVOT_R1',
      'PIVOT_R2',
      'PIVOT_R3',
      'PIVOT_S1',
      'PIVOT_S2',
      'PIVOT_S3',
    ],
    values: [
      decimalValue('p', 'price', 'Pivot (H + L + C) / 3.'),
      decimalValue('r1', 'price', 'Resistance 1, 2P - L.'),
      decimalValue('r2', 'price', 'Resistance 2, P + (H - L).'),
      decimalValue('r3', 'price', 'Resistance 3, H + 2(P - L).'),
      decimalValue('s1', 'price', 'Support 1, 2P - H.'),
      decimalValue('s2', 'price', 'Support 2, P - (H - L).'),
      decimalValue('s3', 'price', 'Support 3, L - 2(H - P).'),
      {
        key: 'period_start_ms',
        type: 'timestamp_ms',
        unit: 'none',
        description: 'Start of the period the levels apply to.',
      },
    ],
    display: display(
      {layer: true, visible: true, z: 20},
      {
        kinds: [
          levelKind('PIVOT_P', 'P', 'neutral'),
          levelKind('PIVOT_R1', 'R1', 'down'),
          levelKind('PIVOT_R2', 'R2', 'down'),
          levelKind('PIVOT_R3', 'R3', 'down'),
          levelKind('PIVOT_S1', 'S1', 'up'),
          levelKind('PIVOT_S2', 'S2', 'up'),
          levelKind('PIVOT_S3', 'S3', 'up'),
        ],
      }
    ),
  },
];

/**
 * Builds the DEMO plugin descriptors.
 * @return One descriptor per built-in plugin, in the engine's load order.
 */
export function demoPluginInfos(): IndicatorPluginInfo[] {
  return DEMO_PLUGINS.map(plugin => ({
    plugin_id: plugin.id,
    indicator_version: plugin.version,
    schema_version: 2,
    display_name: plugin.name,
    description: plugin.description,
    timeframes: [...plugin.timeframes],
    warmup_bars: plugin.warmup,
    required_streams: [...plugin.streams],
    input_schema: inputSchema(plugin.settings),
    output_kinds: [...plugin.kinds],
    output_events: [],
    // Fresh copies, so callers can never change the shared table.
    output_values: structuredClone([...plugin.values]),
    display: structuredClone(plugin.display),
    licensing_status: 'CLEAN_ROOM_FROM_SPEC',
    parity_status: 'SPEC_ONLY',
  }));
}
