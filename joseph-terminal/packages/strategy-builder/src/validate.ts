/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Client-side structural validation mirroring
 * `strategy.schema.json` plus cross-field rules (k ≤ n for a K_OF_N state,
 * sequence windows, zone groups). Given the vocabulary of the installed
 * plugins it also checks that kinds, events, plugins and values exist. The
 * server's `/validate` remains authoritative; this gives immediate feedback
 * and the same JSON-pointer paths.
 */

import {
  Decimal,
  VENUES,
  type Timeframe,
  type ValidationIssue,
  type ValidationResult,
} from '@joseph/contracts';
import {TIMEFRAME_ORDER} from './catalog';
import {
  NAME_PATTERN,
  PLUGIN_ID_PATTERN,
  RESERVED_EVENTS,
  type Vocabulary,
} from './vocabulary';

/** Result of client validation. */
export interface ClientValidation {
  /** True when there are no errors. */
  valid: boolean;
  /** Blocking problems. */
  errors: ValidationIssue[];
  /** Non-blocking advice. */
  warnings: ValidationIssue[];
  /** Indicator plugins the rule depends on, sorted. */
  requiredPlugins: string[];
  /** Timeframes the rule reads (signal timeframe included), shortest first. */
  requiredTimeframes: Timeframe[];
  /** Number of rule nodes. */
  nodeCount: number;
  /** Maximum nesting depth (root = 1). */
  maxDepth: number;
}

/** Loose JSON record. */
type Json = Record<string, unknown>;

/**
 * Codes of the non-blocking issues this validator reports (warnings); every
 * other code it reports is an error.
 */
export const WARNING_CODES: ReadonlySet<string> = new Set([
  'auto_live',
  'deep_tree',
  'k_ignored',
  'group_single',
  'event_source_implicit',
  'unknown_indicator_field',
  'state_value_not_published',
  'target_kinds_ignored',
]);

// Allowed values for the checks below, taken from the contract schemas.
const DIRECTIONS = ['LONG', 'SHORT'];
const MODES = ['ALERT_ONLY', 'PAPER', 'SHADOW', 'AUTO_LIVE'];
const STRATEGY_ID = /^[a-z0-9][a-z0-9-]{0,62}$/;
const INSTRUMENT_ID = new RegExp(
  `^(${VENUES.join('|')}):(perp|spot|future|stock|etf|rate):[A-Za-z0-9._/-]{1,64}$`
);
const LOCAL_TIME = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const VENUE_IDS: readonly string[] = VENUES;
const SIGNAL_KINDS = [
  'WATCHING_AOI',
  'PRE_SIGNAL',
  'ENTRY_CONFIRMED',
  'ENTRY_BLOCKED',
  'AOI_INVALIDATED',
  'SETUP_EXPIRED',
  'QUALIFICATION_CHANGED',
  'POSITION_EXITED',
];
const CHANNELS = ['UI', 'TELEGRAM', 'LINE', 'EMAIL', 'WEBHOOK'];
const NODE_KEYS: Readonly<Record<string, readonly string[]>> = {
  ALL: ['children'],
  ANY: ['children'],
  NOT: ['child'],
  STATE: ['plugin_id', 'timeframes', 'where', 'match', 'k'],
  IN_ZONE: ['kinds', 'side', 'tf', 'price_source', 'group'],
  AOI_TOUCH: [
    'kinds',
    'side',
    'tf',
    'within_bars',
    'touch',
    'require_active',
    'min_quality',
  ],
  AOI_OVERLAP: ['kinds_a', 'kinds_b', 'side', 'tf', 'min_overlap_pct'],
  EVENT: ['name', 'source', 'tf', 'within_bars'],
  SEQUENCE: ['within_bars', 'steps'],
  COMPARE: ['left', 'cmp', 'right'],
  IN_RANGE: ['value', 'low', 'high', 'inclusive'],
  CROSSES: ['left', 'right', 'direction'],
  PERSIST_FOR: ['bars', 'child'],
  WITHIN_BARS: ['bars', 'child'],
  TIME_WINDOW: ['time_zone', 'windows', 'weekdays'],
  RISK_REWARD: ['min_rr'],
};

/**
 * Returns whether a value is a plain object.
 * @param value Value.
 * @return True for non-null, non-array objects.
 */
function isRecord(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Returns whether a value is an integer within bounds.
 * @param value Value.
 * @param min Inclusive minimum.
 * @param max Inclusive maximum.
 * @return True for an integer in `[min, max]`.
 */
function isInt(value: unknown, min: number, max: number): value is number {
  return (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= min &&
    value <= max
  );
}

/**
 * Returns whether a value is a non-negative decimal string.
 * @param value Value.
 * @return True for a valid decimal string that is zero or positive.
 */
function isNonNegDecimal(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    Decimal.isValid(value) &&
    !Decimal.parse(value).isNegative()
  );
}

/**
 * Returns whether an IANA time zone is known to this runtime.
 * @param zone Time zone name, e.g. `America/New_York`.
 * @return True when `Intl` accepts the zone.
 */
function isTimeZone(zone: string): boolean {
  try {
    // The constructor throws a RangeError for an unknown zone.
    void new Intl.DateTimeFormat('en-US', {timeZone: zone});
    return true;
  } catch {
    return false;
  }
}

/**
 * Validates a strategy definition.
 *
 * Never throws: every problem becomes an error (blocking) or a warning
 * (advice), with the JSON-pointer path of the offending value.
 * @param definition Candidate definition (any JSON).
 * @param vocab Vocabulary of the installed plugins. When given, AOI kinds,
 *     events, plugins and published values must exist in it, and the
 *     plugins behind kinds and events count as required; without it only
 *     the structure is checked.
 * @return Validation result; `valid` is true when there are no errors.
 */
export function validateDefinition(
  definition: unknown,
  vocab?: Vocabulary
): ClientValidation {
  const errors: ValidationIssue[] = [];
  const warnings: ValidationIssue[] = [];
  const plugins = new Set<string>();
  const timeframes = new Set<Timeframe>();
  const err = (path: string, code: string, message: string) =>
    errors.push({path, code, message});
  const warn = (path: string, code: string, message: string) =>
    warnings.push({path, code, message});
  let nodeCount = 0;
  let maxDepth = 0;

  if (!isRecord(definition)) {
    err('', 'type', 'The strategy must be a JSON object.');
    return {
      valid: false,
      errors,
      warnings,
      requiredPlugins: [],
      requiredTimeframes: [],
      nodeCount,
      maxDepth,
    };
  }
  const d = definition;
  if (d.schema_version !== 2) {
    err(
      '/schema_version',
      'schema_version',
      'Only rule schema version 2 is supported.'
    );
  }
  if (typeof d.strategy_id !== 'string' || !STRATEGY_ID.test(d.strategy_id)) {
    err(
      '/strategy_id',
      'pattern',
      'Use 1-63 lowercase letters, digits or dashes, starting with a letter or digit.'
    );
  }
  if (
    typeof d.name !== 'string' ||
    d.name.trim().length === 0 ||
    d.name.length > 200
  ) {
    err('/name', 'length', 'Give the strategy a name of up to 200 characters.');
  }
  if (typeof d.description === 'string' && d.description.length > 2000) {
    err(
      '/description',
      'length',
      'Keep the description under 2000 characters.'
    );
  }
  if (!DIRECTIONS.includes(String(d.direction))) {
    err('/direction', 'enum', 'Direction must be LONG or SHORT.');
  }
  const signalTf = TIMEFRAME_ORDER.includes(d.signal_timeframe as Timeframe)
    ? (d.signal_timeframe as Timeframe)
    : null;
  if (!signalTf) {
    err('/signal_timeframe', 'enum', 'Choose a supported signal timeframe.');
  } else timeframes.add(signalTf);
  if (!MODES.includes(String(d.execution_mode))) {
    err('/execution_mode', 'enum', 'Choose an execution mode.');
  }
  if (d.execution_mode === 'AUTO_LIVE') {
    warn(
      '/execution_mode',
      'auto_live',
      'AUTO_LIVE only takes effect through an execution deployment with explicit consent.'
    );
  }

  // Universe.
  if (!isRecord(d.universe)) {
    err('/universe', 'required', 'Define the instrument universe.');
  } else {
    const u = d.universe;
    if (u.qualification !== 'QUALIFIED_ONLY' && u.qualification !== 'ANY') {
      err(
        '/universe/qualification',
        'enum',
        'Qualification must be QUALIFIED_ONLY or ANY.'
      );
    }
    if (!Array.isArray(u.venues) || u.venues.length === 0) {
      err('/universe/venues', 'minItems', 'Choose at least one venue.');
    } else {
      u.venues.forEach((v, i) => {
        if (!VENUE_IDS.includes(String(v))) {
          err(`/universe/venues/${i}`, 'enum', `Unknown venue ${String(v)}.`);
        }
      });
    }
    for (const list of ['whitelist', 'blacklist'] as const) {
      const ids = u[list];
      if (ids === undefined) continue;
      if (!Array.isArray(ids)) {
        err(`/universe/${list}`, 'type', 'Must be a list of instrument ids.');
        continue;
      }
      ids.forEach((id, i) => {
        if (typeof id !== 'string' || !INSTRUMENT_ID.test(id)) {
          err(
            `/universe/${list}/${i}`,
            'pattern',
            'Use exact venue instrument ids such as binance:perp:BTCUSDT.'
          );
        }
      });
    }
    if (
      u.min_qualified_minutes !== undefined &&
      !isInt(u.min_qualified_minutes, 0, Number.MAX_SAFE_INTEGER)
    ) {
      err(
        '/universe/min_qualified_minutes',
        'minimum',
        'Must be a whole number of minutes.'
      );
    }
  }

  const checkKinds = (value: unknown, path: string, max: number) => {
    if (!Array.isArray(value) || value.length === 0) {
      err(path, 'minItems', 'Choose at least one AOI kind.');
      return;
    }
    if (value.length > max) {
      err(path, 'maxItems', `Choose at most ${max} AOI kinds.`);
    }
    value.forEach((kind, i) => {
      if (typeof kind !== 'string' || !NAME_PATTERN.test(kind)) {
        err(
          `${path}/${i}`,
          'pattern',
          'AOI kinds are upper-case names such as ZONE_HIGH.'
        );
        return;
      }
      if (!vocab) return;
      const owner = vocab.kinds.get(kind);
      if (owner) plugins.add(owner.pluginId);
      else {
        err(
          `${path}/${i}`,
          'kind_unknown',
          `No installed indicator plugin publishes AOI kind ${kind}.`
        );
      }
    });
  };

  // Risk model.
  if (!isRecord(d.risk)) {
    err('/risk', 'required', 'Define the risk model.');
  } else {
    const r = d.risk;
    if (!isNonNegDecimal(r.min_rr)) {
      err(
        '/risk/min_rr',
        'decimal',
        'Minimum R:R must be a non-negative number.'
      );
    }
    if (!['AOI_INVALIDATION', 'SWING', 'ATR'].includes(String(r.stop_model))) {
      err('/risk/stop_model', 'enum', 'Choose a stop model.');
    }
    if (!['NEAREST_AOI', 'FIXED_R'].includes(String(r.target_model))) {
      err('/risk/target_model', 'enum', 'Choose a target model.');
    }
    if (r.target_model === 'FIXED_R' && !isNonNegDecimal(r.fixed_r)) {
      err('/risk/fixed_r', 'required', 'A fixed-R target needs an R multiple.');
    }
    if (r.target_model === 'NEAREST_AOI') {
      if (r.target_kinds === undefined) {
        err(
          '/risk/target_kinds',
          'target_kinds_required',
          'Choose the AOI kinds the target may use.'
        );
      } else checkKinds(r.target_kinds, '/risk/target_kinds', 8);
    } else if (r.target_kinds !== undefined) {
      warn(
        '/risk/target_kinds',
        'target_kinds_ignored',
        'Target kinds only apply to the nearest-AOI target.'
      );
    }
    for (const key of [
      'stop_buffer_atr',
      'fee_bps_estimate',
      'max_risk_pct_equity',
    ] as const) {
      if (r[key] !== undefined && !isNonNegDecimal(r[key])) {
        err(`/risk/${key}`, 'decimal', 'Must be a non-negative number.');
      }
    }
  }

  // Notification policy.
  if (!isRecord(d.notification)) {
    err('/notification', 'required', 'Define notification settings.');
  } else {
    const n = d.notification;
    if (!isInt(n.cooldown_bars, 0, 10000)) {
      err(
        '/notification/cooldown_bars',
        'range',
        'Cooldown must be 0 to 10000 bars.'
      );
    }
    if (typeof n.once_per_aoi !== 'boolean') {
      err(
        '/notification/once_per_aoi',
        'type',
        'Choose whether to alert once per AOI.'
      );
    }
    if (
      n.emit !== undefined &&
      (!Array.isArray(n.emit) ||
        n.emit.some(k => !SIGNAL_KINDS.includes(String(k))))
    ) {
      err(
        '/notification/emit',
        'enum',
        'Unknown signal kind in the notification list.'
      );
    }
    if (
      n.channels !== undefined &&
      (!Array.isArray(n.channels) ||
        n.channels.some(c => !CHANNELS.includes(String(c))))
    ) {
      err('/notification/channels', 'enum', 'Unknown notification channel.');
    }
  }
  if (
    d.setup !== undefined &&
    (!isRecord(d.setup) ||
      (d.setup.expire_after_bars !== undefined &&
        !isInt(d.setup.expire_after_bars, 1, 1000)))
  ) {
    err(
      '/setup/expire_after_bars',
      'range',
      'Setups expire after 1 to 1000 bars.'
    );
  }
  if (d.data_policy !== undefined && isRecord(d.data_policy)) {
    if (
      d.data_policy.max_data_age_ms !== undefined &&
      !isInt(d.data_policy.max_data_age_ms, 0, Number.MAX_SAFE_INTEGER)
    ) {
      err(
        '/data_policy/max_data_age_ms',
        'minimum',
        'Maximum data age must be a whole number of milliseconds.'
      );
    }
  }

  // Rule tree.
  const ids = new Set<string>();
  const zoneGroups = new Map<
    string,
    {tf: unknown; count: number; path: string}
  >();
  const checkTf = (value: unknown, path: string, required: boolean) => {
    if (value === undefined && !required) return;
    if (!TIMEFRAME_ORDER.includes(value as Timeframe)) {
      err(path, 'enum', 'Choose a supported timeframe.');
      return;
    }
    timeframes.add(value as Timeframe);
  };
  // Returns whether the plugin id is usable (and installed, given a
  // vocabulary). Operand ids have no pattern in the schema; STATE plugins
  // and event sources do.
  const checkPlugin = (
    value: unknown,
    path: string,
    pattern: boolean
  ): boolean => {
    if (typeof value !== 'string' || !value) {
      err(path, 'required', 'Choose an indicator.');
      return false;
    }
    if (pattern && !PLUGIN_ID_PATTERN.test(value)) {
      err(path, 'pattern', `Unknown indicator plugin id ${value}.`);
      return false;
    }
    plugins.add(value);
    if (vocab && !vocab.plugins.has(value)) {
      err(
        path,
        'plugin_unknown',
        `The indicator plugin ${value} is not installed.`
      );
      return false;
    }
    return true;
  };
  const checkField = (pluginId: string, field: unknown, path: string) => {
    if (typeof field !== 'string' || !field || field.length > 64) {
      err(path, 'required', 'Choose an indicator value.');
      return;
    }
    const info = vocab?.plugins.get(pluginId);
    if (info && !info.output_values.some(v => v.key === field)) {
      warn(
        path,
        'unknown_indicator_field',
        `${info.display_name} does not declare a value named ${field}.`
      );
    }
  };
  const checkOperand = (value: unknown, path: string) => {
    if (!isRecord(value)) {
      err(path, 'type', 'Choose a value to compare.');
      return;
    }
    switch (value.kind) {
      case 'const':
        if (typeof value.value !== 'string' || !Decimal.isValid(value.value)) {
          err(`${path}/value`, 'decimal', 'Enter a number.');
        }
        break;
      case 'candle':
        if (
          !['open', 'high', 'low', 'close', 'base_volume'].includes(
            String(value.field)
          )
        ) {
          err(`${path}/field`, 'enum', 'Choose a candle field.');
        }
        checkTf(value.tf, `${path}/tf`, false);
        break;
      case 'indicator':
        if (checkPlugin(value.plugin_id, `${path}/plugin_id`, false)) {
          checkField(String(value.plugin_id), value.field, `${path}/field`);
        } else if (typeof value.field !== 'string' || !value.field) {
          err(`${path}/field`, 'required', 'Choose an indicator value.');
        }
        checkTf(value.tf, `${path}/tf`, false);
        break;
      case 'market':
        if (
          ![
            'funding_8h_pct',
            'open_interest_usd',
            'oi_change_pct',
            'spread_pct',
            'turnover_24h_quote',
          ].includes(String(value.field))
        ) {
          err(`${path}/field`, 'enum', 'Choose a market value.');
        }
        break;
      default:
        err(`${path}/kind`, 'enum', 'Unknown value type.');
    }
    if (value.bars_ago !== undefined && !isInt(value.bars_ago, 0, 200)) {
      err(`${path}/bars_ago`, 'range', 'Bars ago must be 0 to 200.');
    }
  };
  const checkWhere = (
    value: unknown,
    path: string,
    pluginId: string | null
  ) => {
    if (!Array.isArray(value) || value.length === 0 || value.length > 8) {
      err(path, 'items', 'Add 1 to 8 value conditions.');
      return;
    }
    const info = pluginId ? vocab?.plugins.get(pluginId) : undefined;
    value.forEach((clause, i) => {
      const at = `${path}/${i}`;
      if (!isRecord(clause)) {
        err(at, 'type', 'Each value condition names a value and its matches.');
        return;
      }
      for (const key of Object.keys(clause)) {
        if (key !== 'field' && key !== 'any_of') {
          err(
            `${at}/${key}`,
            'additionalProperties',
            `Unknown field "${key}".`
          );
        }
      }
      const field = clause.field;
      const anyOf = clause.any_of;
      if (typeof field !== 'string' || !field || field.length > 64) {
        err(`${at}/field`, 'required', 'Choose an indicator value.');
      }
      if (
        !Array.isArray(anyOf) ||
        anyOf.length === 0 ||
        anyOf.length > 16 ||
        anyOf.some(v => typeof v !== 'string' && typeof v !== 'boolean')
      ) {
        err(
          `${at}/any_of`,
          'items',
          'Choose 1 to 16 values (text or true/false).'
        );
        return;
      }
      if (!info || typeof field !== 'string') return;
      const spec = info.output_values.find(v => v.key === field);
      if (!spec) {
        warn(
          `${at}/field`,
          'unknown_indicator_field',
          `${info.display_name} does not declare a value named ${field}.`
        );
      } else if (spec.type !== 'string' && spec.type !== 'boolean') {
        warn(
          `${at}/field`,
          'state_value_not_published',
          `${field} is a ${spec.type} value; a state matches text or true/false values.`
        );
      } else {
        anyOf.forEach((v, j) => {
          const expected = spec.type === 'boolean' ? 'boolean' : 'string';
          const choices = spec.choices ?? [];
          if (
            typeof v !== expected ||
            (choices.length > 0 && !choices.includes(String(v)))
          ) {
            warn(
              `${at}/any_of/${j}`,
              'state_value_not_published',
              `${info.display_name} never publishes ${String(v)} as ${field}.`
            );
          }
        });
      }
    });
  };
  const checkEvent = (step: Json, path: string) => {
    const name = step.name;
    if (typeof name !== 'string' || !NAME_PATTERN.test(name)) {
      err(
        `${path}/name`,
        'pattern',
        'Event names are upper-case names such as ZONE_FORMED.'
      );
      return;
    }
    const source = step.source;
    if (source !== undefined && !checkPlugin(source, `${path}/source`, true)) {
      return;
    }
    if (!vocab) return;
    const reserved = name in RESERVED_EVENTS;
    const publishers = vocab.events.get(name)?.pluginIds ?? [];
    if (!reserved && publishers.length === 0) {
      err(
        `${path}/name`,
        'event_unknown',
        `No installed indicator plugin publishes the event ${name}.`
      );
      return;
    }
    if (typeof source === 'string') {
      if (!reserved && !publishers.includes(source)) {
        err(
          `${path}/source`,
          'event_source_mismatch',
          `${vocab.plugins.get(source)?.display_name ?? source} does not publish ${name}.`
        );
      }
      return;
    }
    if (reserved) return;
    const [first] = publishers;
    if (first) plugins.add(first);
    if (publishers.length > 1) {
      warn(
        `${path}/source`,
        'event_source_implicit',
        `Several indicators publish ${name}; events from any of them count. Choose a source to use only one.`
      );
    }
  };

  const visit = (node: unknown, path: string, depth: number) => {
    if (!isRecord(node)) {
      err(path, 'type', 'Each condition must be an object.');
      return;
    }
    nodeCount += 1;
    maxDepth = Math.max(maxDepth, depth);
    const op = String(node.op);
    const allowed = NODE_KEYS[op];
    if (!allowed) {
      err(
        `${path}/op`,
        'unsupported_node',
        `Condition type "${op}" is not supported. It must be replaced; it is never skipped.`
      );
      return;
    }
    for (const key of Object.keys(node)) {
      if (
        key !== 'op' &&
        key !== 'id' &&
        key !== 'label' &&
        !allowed.includes(key)
      ) {
        err(
          `${path}/${key}`,
          'additionalProperties',
          `Unknown field "${key}" on ${op}.`
        );
      }
    }
    if (node.id !== undefined) {
      if (typeof node.id !== 'string' || node.id.length > 64) {
        err(`${path}/id`, 'type', 'Node ids are strings up to 64 characters.');
      } else if (ids.has(node.id)) {
        err(`${path}/id`, 'duplicate_id', `Duplicate node id ${node.id}.`);
      } else ids.add(node.id);
    }
    if (
      node.label !== undefined &&
      (typeof node.label !== 'string' || node.label.length > 200)
    ) {
      err(`${path}/label`, 'length', 'Labels are up to 200 characters.');
    }
    switch (op) {
      case 'ALL':
      case 'ANY': {
        const children = node.children;
        if (!Array.isArray(children) || children.length === 0) {
          err(
            `${path}/children`,
            'minItems',
            'A group needs at least one condition.'
          );
          return;
        }
        if (children.length > 32) {
          err(
            `${path}/children`,
            'maxItems',
            'A group holds at most 32 conditions.'
          );
        }
        children.forEach(
          (child, i) => void visit(child, `${path}/children/${i}`, depth + 1)
        );
        return;
      }
      case 'NOT':
        visit(node.child, `${path}/child`, depth + 1);
        return;
      case 'PERSIST_FOR':
        if (!isInt(node.bars, 1, 200)) {
          err(`${path}/bars`, 'range', 'Bars must be 1 to 200.');
        }
        visit(node.child, `${path}/child`, depth + 1);
        return;
      case 'WITHIN_BARS':
        if (!isInt(node.bars, 0, 200)) {
          err(`${path}/bars`, 'range', 'Bars must be 0 to 200.');
        }
        visit(node.child, `${path}/child`, depth + 1);
        return;
      case 'STATE': {
        const pluginOk = checkPlugin(node.plugin_id, `${path}/plugin_id`, true);
        const tfs = node.timeframes;
        if (!Array.isArray(tfs) || tfs.length === 0 || tfs.length > 6) {
          err(`${path}/timeframes`, 'items', 'Choose 1 to 6 timeframes.');
        } else {
          tfs.forEach(
            (tf, i) => void checkTf(tf, `${path}/timeframes/${i}`, true)
          );
          if (new Set(tfs).size !== tfs.length) {
            err(
              `${path}/timeframes`,
              'uniqueItems',
              'Each timeframe may appear once.'
            );
          }
        }
        checkWhere(
          node.where,
          `${path}/where`,
          pluginOk ? String(node.plugin_id) : null
        );
        if (
          node.match !== undefined &&
          node.match !== 'ALL' &&
          node.match !== 'K_OF_N'
        ) {
          err(`${path}/match`, 'enum', 'Match must be ALL or K_OF_N.');
        }
        if (node.match === 'K_OF_N') {
          const n = Array.isArray(tfs) ? tfs.length : 0;
          if (!isInt(node.k, 1, 6)) {
            err(
              `${path}/k`,
              'required',
              'Choose how many timeframes must agree.'
            );
          } else if (node.k > n) {
            err(
              `${path}/k`,
              'k_exceeds_n',
              `k (${node.k}) cannot exceed the number of timeframes (${n}).`
            );
          }
        } else if (node.k !== undefined) {
          warn(
            `${path}/k`,
            'k_ignored',
            'k only applies when at least k timeframes must agree.'
          );
        }
        return;
      }
      case 'IN_ZONE':
        checkKinds(node.kinds, `${path}/kinds`, 8);
        if (
          node.side !== undefined &&
          node.side !== 'bullish' &&
          node.side !== 'bearish'
        ) {
          err(`${path}/side`, 'enum', 'Choose bullish or bearish.');
        }
        checkTf(node.tf, `${path}/tf`, true);
        if (
          node.price_source !== undefined &&
          !['close', 'high', 'low', 'hl2'].includes(String(node.price_source))
        ) {
          err(`${path}/price_source`, 'enum', 'Choose a price source.');
        }
        if (node.group !== undefined) {
          if (
            typeof node.group !== 'string' ||
            node.group.length === 0 ||
            node.group.length > 32
          ) {
            err(`${path}/group`, 'length', 'Groups are 1 to 32 characters.');
          } else {
            const existing = zoneGroups.get(node.group);
            if (!existing) {
              zoneGroups.set(node.group, {tf: node.tf, count: 1, path});
            } else {
              existing.count += 1;
              if (existing.tf !== node.tf) {
                err(
                  `${path}/tf`,
                  'group_timeframe',
                  `Group ${node.group} mixes timeframes; zones that must share a group id must share a timeframe.`
                );
              }
            }
          }
        }
        return;
      case 'AOI_TOUCH':
        checkKinds(node.kinds, `${path}/kinds`, 8);
        if (node.side !== 'bullish' && node.side !== 'bearish') {
          err(`${path}/side`, 'enum', 'Choose bullish or bearish.');
        }
        checkTf(node.tf, `${path}/tf`, false);
        if (!isInt(node.within_bars, 0, 200)) {
          err(
            `${path}/within_bars`,
            'range',
            'Lookback must be 0 to 200 bars.'
          );
        }
        if (
          node.touch !== undefined &&
          !['WICK', 'BODY', 'CLOSE'].includes(String(node.touch))
        ) {
          err(`${path}/touch`, 'enum', 'Touch must be wick, body or close.');
        }
        if (
          node.require_active !== undefined &&
          typeof node.require_active !== 'boolean'
        ) {
          err(`${path}/require_active`, 'type', 'Must be true or false.');
        }
        if (
          node.min_quality !== undefined &&
          !isInt(node.min_quality, 0, 100)
        ) {
          err(`${path}/min_quality`, 'range', 'Quality is 0 to 100.');
        }
        return;
      case 'AOI_OVERLAP':
        checkKinds(node.kinds_a, `${path}/kinds_a`, 32);
        checkKinds(node.kinds_b, `${path}/kinds_b`, 32);
        if (node.side !== 'bullish' && node.side !== 'bearish') {
          err(`${path}/side`, 'enum', 'Choose bullish or bearish.');
        }
        checkTf(node.tf, `${path}/tf`, false);
        if (
          node.min_overlap_pct !== undefined &&
          !isNonNegDecimal(node.min_overlap_pct)
        ) {
          err(
            `${path}/min_overlap_pct`,
            'decimal',
            'Enter a non-negative percentage.'
          );
        }
        return;
      case 'EVENT':
        checkEvent(node, path);
        checkTf(node.tf, `${path}/tf`, false);
        if (!isInt(node.within_bars, 0, 200)) {
          err(
            `${path}/within_bars`,
            'range',
            'Lookback must be 0 to 200 bars.'
          );
        }
        return;
      case 'SEQUENCE': {
        const steps = node.steps;
        if (!Array.isArray(steps) || steps.length < 2 || steps.length > 8) {
          err(`${path}/steps`, 'items', 'A sequence has 2 to 8 steps.');
        } else {
          steps.forEach((step, i) => {
            if (!isRecord(step)) {
              err(`${path}/steps/${i}`, 'type', 'Each step names an event.');
              return;
            }
            checkEvent(step, `${path}/steps/${i}`);
            checkTf(step.tf, `${path}/steps/${i}/tf`, false);
          });
        }
        if (!isInt(node.within_bars, 1, 200)) {
          err(
            `${path}/within_bars`,
            'range',
            'The window must be 1 to 200 bars.'
          );
        } else if (Array.isArray(steps) && node.within_bars < steps.length) {
          err(
            `${path}/within_bars`,
            'window_too_small',
            `Steps happen on distinct bars, so the window needs at least ${steps.length} bars.`
          );
        }
        return;
      }
      case 'COMPARE':
        checkOperand(node.left, `${path}/left`);
        checkOperand(node.right, `${path}/right`);
        if (
          !['gt', 'gte', 'lt', 'lte', 'eq', 'neq'].includes(String(node.cmp))
        ) {
          err(`${path}/cmp`, 'enum', 'Choose a comparison.');
        }
        return;
      case 'IN_RANGE':
        checkOperand(node.value, `${path}/value`);
        checkOperand(node.low, `${path}/low`);
        checkOperand(node.high, `${path}/high`);
        return;
      case 'CROSSES':
        checkOperand(node.left, `${path}/left`);
        checkOperand(node.right, `${path}/right`);
        if (node.direction !== 'ABOVE' && node.direction !== 'BELOW') {
          err(`${path}/direction`, 'enum', 'Choose above or below.');
        }
        return;
      case 'TIME_WINDOW': {
        if (
          typeof node.time_zone !== 'string' ||
          node.time_zone.length === 0 ||
          node.time_zone.length > 64
        ) {
          err(`${path}/time_zone`, 'required', 'Choose a time zone.');
        } else if (!isTimeZone(node.time_zone)) {
          err(
            `${path}/time_zone`,
            'time_zone_unknown',
            `Unknown time zone ${node.time_zone}.`
          );
        }
        const windows = node.windows;
        if (!Array.isArray(windows) || windows.length === 0) {
          err(`${path}/windows`, 'minItems', 'Add at least one time window.');
        } else {
          if (windows.length > 8) {
            err(`${path}/windows`, 'maxItems', 'Use at most 8 time windows.');
          }
          windows.forEach((w, i) => {
            const at = `${path}/windows/${i}`;
            if (!isRecord(w)) {
              err(at, 'type', 'Each window has a start and an end.');
              return;
            }
            for (const key of ['start', 'end'] as const) {
              if (typeof w[key] !== 'string' || !LOCAL_TIME.test(w[key])) {
                err(`${at}/${key}`, 'pattern', 'Use a 24-hour time as HH:MM.');
              }
            }
            if (w.start === w.end) {
              err(
                `${at}/end`,
                'time_window_empty',
                'The window ends when it starts, so it never matches.'
              );
            }
          });
        }
        const days = node.weekdays;
        if (days !== undefined) {
          if (
            !Array.isArray(days) ||
            days.length === 0 ||
            days.length > 7 ||
            days.some(day => !isInt(day, 1, 7))
          ) {
            err(
              `${path}/weekdays`,
              'items',
              'Weekdays are 1 (Monday) to 7 (Sunday); leave them out for every day.'
            );
          } else if (new Set(days).size !== days.length) {
            err(
              `${path}/weekdays`,
              'uniqueItems',
              'Each weekday may appear once.'
            );
          }
        }
        return;
      }
      case 'RISK_REWARD':
        if (!isNonNegDecimal(node.min_rr)) {
          err(
            `${path}/min_rr`,
            'decimal',
            'Minimum R:R must be a non-negative number.'
          );
        }
        return;
      default:
        // Unreachable: unknown operators were reported above.
        return;
    }
  };
  if (d.when === undefined) {
    err('/when', 'required', 'Add at least one condition.');
  } else visit(d.when, '/when', 1);
  for (const [group, info] of zoneGroups) {
    if (info.count === 1) {
      warn(
        `${info.path}/group`,
        'group_single',
        `Group ${group} has a single zone condition, so it binds nothing.`
      );
    }
  }
  if (maxDepth > 8) {
    warn(
      '/when',
      'deep_tree',
      'Rules nested deeper than 8 levels are hard to review.'
    );
  }

  return {
    valid: errors.length === 0,
    errors,
    warnings,
    requiredPlugins: [...plugins].sort(),
    requiredTimeframes: TIMEFRAME_ORDER.filter(tf => timeframes.has(tf)),
    nodeCount,
    maxDepth,
  };
}

/**
 * Finds the id of the deepest rule node on a JSON pointer.
 * @param definition Strategy definition.
 * @param pointer JSON pointer such as `/when/children/3/within_bars`.
 * @return Id of the deepest node on the path that has one, or null when the
 *     pointer is outside `/when` or no node on the path has an id.
 */
export function nodeIdAtPointer(
  definition: unknown,
  pointer: string
): string | null {
  if (!isRecord(definition) || !pointer.startsWith('/when')) return null;
  const parts = pointer.split('/').slice(2);
  let current: unknown = definition.when;
  let lastId: string | null =
    isRecord(current) && typeof current.id === 'string' ? current.id : null;
  for (const part of parts) {
    if (Array.isArray(current)) current = current[Number(part)];
    else if (isRecord(current)) current = current[part];
    else break;
    if (
      isRecord(current) &&
      typeof current.op === 'string' &&
      typeof current.id === 'string'
    ) {
      lastId = current.id;
    }
  }
  return lastId;
}

/**
 * Validates a definition and returns the contract `ValidationResult` shape
 * (used by DEMO services; the server computes the real definition hash).
 * @param definition Candidate definition.
 * @param definitionHash Hash to report when valid, or null.
 * @param vocab Vocabulary of the installed plugins; see
 *     `validateDefinition`.
 * @return Contract validation result; `definition_hash` is null when the
 *     definition is invalid.
 */
export function toValidationResult(
  definition: unknown,
  definitionHash: string | null = null,
  vocab?: Vocabulary
): ValidationResult {
  const result = validateDefinition(definition, vocab);
  return {
    valid: result.valid,
    errors: result.errors,
    warnings: result.warnings,
    required_plugins: result.requiredPlugins,
    required_timeframes: result.requiredTimeframes,
    node_count: result.nodeCount,
    max_depth: result.maxDepth,
    definition_hash: result.valid ? definitionHash : null,
  };
}
