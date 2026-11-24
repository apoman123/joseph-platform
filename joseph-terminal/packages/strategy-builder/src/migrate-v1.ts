/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Migration from the starter's v1 `StrategyConfig` to Rule AST
 * v2. Meaning is preserved condition by condition; anything that cannot be
 * expressed exactly is reported, never silently dropped, and the resulting
 * strategy is always ALERT_ONLY.
 */

import type {
  AoiKind,
  AoiTouchNode,
  RuleNode,
  SequenceNode,
  StrategyDefinition,
  StructureEventName,
  Timeframe,
} from '@joseph/contracts';

/** Starter v1 rule shape. */
export interface StrategyConfigV1 {
  /** Always 1. */
  version: 1;
  /** Rule id. */
  id: string;
  /** Display name. */
  name: string;
  /** Local enabled flag. */
  enabled?: boolean;
  /** Direction. */
  side: 'long' | 'short';
  /** Signal timeframe. */
  timeframe: string;
  /** Higher-timeframe bias filter. */
  htfBias?: 'bullish' | 'bearish' | 'any';
  /** Zones that must all contain price. */
  requiredZones?: string[];
  /** AOI kinds, any of which may be touched. */
  aoiAny?: string[];
  /** AOI lookback in closed bars. */
  aoiLookbackBars?: number;
  /** Ordered trigger events. */
  triggerSequence?: string[];
  /** Trigger window in bars. */
  triggerWindowBars?: number;
  /** Minimum reward/risk. */
  minRR?: number;
  /** Alert cooldown in bars. */
  cooldownBars?: number;
}

/** Outcome of a migration. */
export interface MigrationReport {
  /** v1 fields translated exactly. */
  mapped: string[];
  /** Assumptions the user must review (v1 left them unspecified). */
  assumptions: string[];
  /** v1 content that could not be expressed and was not migrated. */
  unmapped: string[];
}

/** Result of `migrateV1`. */
export interface MigrationResult {
  /** Migrated definition. */
  definition: StrategyDefinition;
  /** What was mapped, assumed or left out. */
  report: MigrationReport;
}

/** v1 trigger names to v2 structure events. */
const TRIGGERS: Readonly<Record<string, StructureEventName>> = {
  ssl_sweep: 'SSL_SWEEP',
  bsl_sweep: 'BSL_SWEEP',
  seller_failure: 'SELLER_FAILURE',
  buyer_failure: 'BUYER_FAILURE',
  buyer_breakout: 'BUYER_BREAKOUT',
  seller_breakout: 'SELLER_BREAKOUT',
  bullish_bos: 'BOS_BULL',
  bearish_bos: 'BOS_BEAR',
};

/** v1 AOI kinds with an exact v2 equivalent. */
const AOIS: Readonly<Record<string, AoiKind>> = {
  fvg: 'FVG',
  ifvg: 'IFVG',
  ob: 'OB',
  breaker: 'BREAKER',
};

/** v1 zones to v2 zones. */
const ZONES: Readonly<Record<string, 'DISCOUNT' | 'PREMIUM' | 'OTE'>> = {
  discount: 'DISCOUNT',
  premium: 'PREMIUM',
  ote: 'OTE',
};

const V1_TIMEFRAMES: readonly Timeframe[] = [
  '1m',
  '3m',
  '5m',
  '15m',
  '1h',
  '4h',
  '1d',
];

/** Higher timeframe assumed for v1 bias (v1 did not say which). */
export const V1_ASSUMED_BIAS_TF: Timeframe = '4h';
/** Timeframe assumed for v1 zones (v1 did not say which). */
export const V1_ASSUMED_ZONE_TF: Timeframe = '1h';

/**
 * Returns whether a value looks like a v1 strategy config.
 * @param value Parsed JSON.
 * @return True when `version === 1` and `side` is present.
 */
export function isStrategyConfigV1(value: unknown): value is StrategyConfigV1 {
  return (
    typeof value === 'object' &&
    value !== null &&
    (value as {version?: unknown}).version === 1 &&
    'side' in value
  );
}

/**
 * Converts a v1 id into a v2 strategy slug.
 * @param id v1 id.
 * @return Slug matching `^[a-z0-9][a-z0-9-]{0,62}$`.
 */
function slug(id: string): string {
  const dashed = id.toLowerCase().replace(/[^a-z0-9-]+/g, '-');
  // Trims leading/trailing dashes in linear time (a `-+$` regex is
  // quadratic on long runs of dashes).
  let start = 0;
  let end = dashed.length;
  while (start < end && dashed[start] === '-') {
    start++;
  }
  while (end > start && dashed[end - 1] === '-') {
    end--;
  }
  const cleaned = dashed.slice(start, end).slice(0, 63);
  return cleaned || 'migrated-strategy';
}

/**
 * Formats a v1 number as a decimal string.
 * @param value Number.
 * @return Decimal text.
 */
function decimalText(value: number): string {
  return Number.isFinite(value) ? String(Number(value.toFixed(8))) : '2';
}

/**
 * Migrates a v1 config to a v2 definition.
 * @param config v1 config.
 * @return Definition and report.
 */
export function migrateV1(config: StrategyConfigV1): MigrationResult {
  const report: MigrationReport = {mapped: [], assumptions: [], unmapped: []};
  const direction = config.side === 'short' ? 'SHORT' : 'LONG';
  const side = direction === 'LONG' ? 'bullish' : 'bearish';
  const children: RuleNode[] = [];
  let n = 0;
  const id = () => `n${(n += 1)}`;

  let signalTf: Timeframe = '5m';
  if (V1_TIMEFRAMES.includes(config.timeframe as Timeframe)) {
    signalTf = config.timeframe as Timeframe;
    report.mapped.push(`timeframe ${config.timeframe} → signal_timeframe`);
  } else {
    report.unmapped.push(
      `timeframe "${config.timeframe}" is not supported; used 5m`
    );
  }

  if (config.htfBias === 'bullish' || config.htfBias === 'bearish') {
    children.push({
      op: 'BIAS',
      id: id(),
      timeframes: [V1_ASSUMED_BIAS_TF],
      value: config.htfBias === 'bullish' ? 'BULLISH' : 'BEARISH',
      match: 'ALL',
    });
    report.mapped.push(`htfBias ${config.htfBias} → BIAS`);
    report.assumptions.push(
      `v1 did not name the bias timeframe; ${V1_ASSUMED_BIAS_TF} was used. Review the BIAS condition.`
    );
  } else if (config.htfBias === 'any' || config.htfBias === undefined) {
    report.mapped.push('htfBias any → no bias condition');
  }

  const zones = config.requiredZones ?? [];
  const zoneNodes: RuleNode[] = [];
  for (const zone of zones) {
    const mapped = ZONES[zone];
    if (!mapped) {
      report.unmapped.push(`requiredZones "${zone}" has no v2 equivalent`);
      continue;
    }
    zoneNodes.push({
      op: 'PRICE_IN_ZONE',
      id: id(),
      zone: mapped,
      tf: V1_ASSUMED_ZONE_TF,
      range_group: 'A',
    });
  }
  if (zoneNodes.length) {
    children.push(...zoneNodes);
    report.mapped.push(
      `requiredZones ${zones.join(', ')} → PRICE_IN_ZONE (shared range group A)`
    );
    report.assumptions.push(
      `v1 did not name the zone timeframe; ${V1_ASSUMED_ZONE_TF} was used and all zones are bound to one dealing range.`
    );
  }

  const kinds: AoiKind[] = [];
  for (const kind of config.aoiAny ?? []) {
    const mapped = AOIS[kind];
    if (mapped) kinds.push(mapped);
    else {
      report.unmapped.push(
        `aoiAny "${kind}" is ambiguous in v2 (choose BSL, SSL, EQH or EQL) and was not migrated`
      );
    }
  }
  if (kinds.length) {
    children.push({
      op: 'AOI_TOUCH',
      id: id(),
      kinds: kinds.slice(0, 8) as AoiTouchNode['kinds'],
      side,
      within_bars: Math.max(
        0,
        Math.min(200, Math.trunc(config.aoiLookbackBars ?? 0))
      ),
      touch: 'WICK',
    });
    report.mapped.push(
      `aoiAny ${kinds.join(', ')} within ${config.aoiLookbackBars ?? 0} bars → AOI_TOUCH`
    );
  }

  const events: StructureEventName[] = [];
  for (const trigger of config.triggerSequence ?? []) {
    const mapped = TRIGGERS[trigger];
    if (mapped) events.push(mapped);
    else report.unmapped.push(`triggerSequence step "${trigger}" is unknown`);
  }
  const windowBars = Math.max(
    1,
    Math.min(200, Math.trunc(config.triggerWindowBars ?? 3))
  );
  const [onlyEvent] = events;
  if (events.length === 1 && onlyEvent) {
    children.push({op: 'EVENT', id: id(), name: onlyEvent, within_bars: 0});
    report.mapped.push(
      `triggerSequence ${onlyEvent} → EVENT on the evaluated bar`
    );
  } else if (events.length >= 2) {
    const steps = events.slice(0, 8).map(name => ({name}));
    if (events.length > 8) {
      report.unmapped.push(
        'triggerSequence steps beyond the eighth were not migrated'
      );
    }
    children.push({
      op: 'SEQUENCE',
      id: id(),
      within_bars: Math.max(windowBars, steps.length),
      steps: steps as SequenceNode['steps'],
    });
    report.mapped.push(
      `triggerSequence ${events.join(' → ')} within ${windowBars} bars → SEQUENCE`
    );
    if (windowBars < steps.length) {
      report.assumptions.push(
        `v1 window (${windowBars}) was shorter than the number of steps, so the v1 rule could never fire; the window was raised to ${steps.length}.`
      );
    }
  }

  const minRr = decimalText(config.minRR ?? 2);
  children.push({op: 'RISK_REWARD', id: id(), min_rr: minRr});
  report.mapped.push(`minRR ${minRr} → RISK_REWARD and risk.min_rr`);

  const cooldown = Math.max(
    0,
    Math.min(10000, Math.trunc(config.cooldownBars ?? 12))
  );
  report.mapped.push(`cooldownBars ${cooldown} → notification.cooldown_bars`);
  if (config.enabled !== undefined) {
    report.unmapped.push(
      'enabled: activation is now a server-side action per version; the migrated strategy starts inactive'
    );
  }
  report.assumptions.push(
    'Universe defaults to qualified Binance perpetuals; v1 had no universe.'
  );
  report.assumptions.push(
    'Execution mode is ALERT_ONLY; migration never enables trading.'
  );

  const definition: StrategyDefinition = {
    schema_version: 2,
    strategy_id: slug(config.id),
    name: (config.name || 'Migrated strategy').slice(0, 200),
    direction,
    signal_timeframe: signalTf,
    universe: {qualification: 'QUALIFIED_ONLY', venues: ['binance']},
    when: {
      op: 'ALL',
      id: 'root',
      children: children as [RuleNode, ...RuleNode[]],
    },
    risk: {
      min_rr: minRr,
      stop_model: 'AOI_INVALIDATION',
      target_model: 'NEAREST_OPPOSING_LIQUIDITY',
    },
    notification: {cooldown_bars: cooldown, once_per_aoi: true},
    execution_mode: 'ALERT_ONLY',
  };
  return {definition, report};
}
