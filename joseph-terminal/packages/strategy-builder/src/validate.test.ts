/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Client validation mirrors the schema and cross-field rules;
 * with a vocabulary it also checks kinds, events, plugins and values.
 */

import type {RiskModel, StrategyDefinition} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {STRATEGY_TEMPLATES} from './templates';
import {readFixture} from './testing/schema';
import {
  fixturePluginInfos,
  pluginInfo,
  testVocabulary,
} from './testing/vocabulary';
import {nodeIdAtPointer, validateDefinition, WARNING_CODES} from './validate';
import {buildVocabulary, type Vocabulary} from './vocabulary';

const VOCAB = testVocabulary();

/**
 * Creates a template by index.
 * @param index Template index.
 * @return Definition.
 * @throws `Error` if the template is missing.
 */
function template(index: number): StrategyDefinition {
  const def = STRATEGY_TEMPLATES[index]?.create();
  if (!def) throw new Error('missing template');
  return def;
}

/**
 * Builds the EMA trend template with a replaced rule tree.
 * @param when Rule tree JSON, often invalid on purpose.
 * @return Definition.
 */
function withWhen(when: unknown): StrategyDefinition {
  // Unchecked so tests can feed trees the validator must reject.
  return {...template(0), when: when as StrategyDefinition['when']};
}

/**
 * Returns the error codes of a definition.
 * @param def Candidate definition.
 * @param vocab Vocabulary, or undefined for structural checks only.
 * @return Error codes in report order.
 */
function codes(def: unknown, vocab: Vocabulary | undefined): string[] {
  return validateDefinition(def, vocab).errors.map(e => e.code);
}

/**
 * Returns the warning codes and paths of a definition.
 * @param def Candidate definition.
 * @param vocab Vocabulary, or undefined for structural checks only.
 * @return `code@path` strings in report order.
 */
function warnings(def: unknown, vocab: Vocabulary | undefined): string[] {
  return validateDefinition(def, vocab).warnings.map(
    w => `${w.code}@${w.path}`
  );
}

describe('validateDefinition', () => {
  it('reports dependencies of the templates', () => {
    const result = validateDefinition(template(0));
    expect(result.valid).toBe(true);
    expect(result.requiredPlugins).toEqual(['ema', 'rsi', 'volume_delta']);
    expect(result.requiredTimeframes).toEqual(['15m', '1h']);
    expect(result.nodeCount).toBe(5);
    expect(result.maxDepth).toBe(2);
    // Kinds name their plugin only through the vocabulary.
    expect(validateDefinition(template(2)).requiredPlugins).toEqual([
      'volume_delta',
    ]);
    expect(validateDefinition(template(2), VOCAB).requiredPlugins).toEqual([
      'pivot_points',
      'volume_delta',
    ]);
  });

  it('rejects every invalid fixture', () => {
    for (const invalid of readFixture('strategy').invalid) {
      expect(validateDefinition(invalid).valid).toBe(false);
    }
  });

  it('checks state timeframes and k', () => {
    const state = {
      op: 'STATE',
      plugin_id: 'example_zones',
      timeframes: ['4h'],
      where: [{field: 'last_break', any_of: ['up']}],
    };
    expect(codes(withWhen({...state, match: 'K_OF_N', k: 2}), VOCAB)).toEqual([
      'k_exceeds_n',
    ]);
    expect(codes(withWhen({...state, match: 'K_OF_N'}), VOCAB)).toEqual([
      'required',
    ]);
    expect(warnings(withWhen({...state, k: 1}), VOCAB)).toEqual([
      'k_ignored@/when/k',
    ]);
    expect(
      codes(withWhen({...state, timeframes: ['4h', '4h']}), VOCAB)
    ).toEqual(['uniqueItems']);
    expect(codes(withWhen({...state, where: []}), VOCAB)).toEqual(['items']);
    expect(
      codes(
        withWhen({...state, where: [{field: 'last_break', any_of: [1]}]}),
        VOCAB
      )
    ).toEqual(['items']);
  });

  it('checks state plugins and published values against the vocabulary', () => {
    const state = (pluginId: string, field: string, anyOf: unknown[]) =>
      withWhen({
        op: 'STATE',
        plugin_id: pluginId,
        timeframes: ['1h'],
        where: [{field, any_of: anyOf}],
      });
    expect(codes(state('no_such_plugin', 'x', ['a']), VOCAB)).toEqual([
      'plugin_unknown',
    ]);
    expect(codes(state('no_such_plugin', 'x', ['a']), undefined)).toEqual([]);
    expect(warnings(state('example_zones', 'nope', ['a']), VOCAB)).toEqual([
      'unknown_indicator_field@/when/where/0/field',
    ]);
    expect(
      warnings(state('example_zones', 'last_break', ['sideways']), VOCAB)
    ).toEqual(['state_value_not_published@/when/where/0/any_of/0']);
    expect(warnings(state('example_zones', 'armed', ['true']), VOCAB)).toEqual([
      'state_value_not_published@/when/where/0/any_of/0',
    ]);
    expect(
      warnings(state('example_zones', 'active_zones', ['3']), VOCAB)
    ).toEqual(['state_value_not_published@/when/where/0/field']);
    expect(
      validateDefinition(state('example_zones', 'armed', [false]), VOCAB)
        .warnings
    ).toEqual([]);
  });

  it('checks AOI kinds against the vocabulary only when given one', () => {
    const touch = withWhen({
      op: 'AOI_TOUCH',
      kinds: ['PIVOT_S1', 'MISSING_KIND'],
      side: 'bullish',
      within_bars: 3,
    });
    expect(
      validateDefinition(touch, VOCAB).errors.map(e => `${e.code}@${e.path}`)
    ).toEqual(['kind_unknown@/when/kinds/1']);
    expect(codes(touch, undefined)).toEqual([]);
    expect(
      codes(withWhen({op: 'IN_ZONE', kinds: ['pivot_s1'], tf: '1h'}), undefined)
    ).toEqual(['pattern']);
  });

  it('requires zone-group members to share a timeframe', () => {
    const zone = (tf: string, group: string) => ({
      op: 'IN_ZONE',
      kinds: ['EXAMPLE_ZONE'],
      tf,
      group,
    });
    const mixed = validateDefinition(
      withWhen({op: 'ALL', children: [zone('1h', 'A'), zone('4h', 'A')]}),
      VOCAB
    );
    expect(mixed.errors.map(e => `${e.code}@${e.path}`)).toEqual([
      'group_timeframe@/when/children/1/tf',
    ]);
    expect(
      warnings(
        withWhen({op: 'ALL', children: [zone('1h', 'A'), zone('1h', 'B')]}),
        VOCAB
      )
    ).toEqual([
      'group_single@/when/children/0/group',
      'group_single@/when/children/1/group',
    ]);
  });

  it('checks events and their sources', () => {
    const event = (name: string, source: string | undefined) =>
      withWhen({op: 'EVENT', name, source, within_bars: 2});
    expect(codes(event('NO_SUCH_EVENT', undefined), VOCAB)).toEqual([
      'event_unknown',
    ]);
    expect(codes(event('NO_SUCH_EVENT', undefined), undefined)).toEqual([]);
    expect(codes(event('bad name', undefined), undefined)).toEqual(['pattern']);
    expect(codes(event('EXAMPLE_BREAK_UP', 'pivot_points'), VOCAB)).toEqual([
      'event_source_mismatch',
    ]);
    expect(codes(event('EXAMPLE_BREAK_UP', 'Bad-Id'), undefined)).toEqual([
      'pattern',
    ]);
    const reserved = validateDefinition(event('AOI_EXPIRED', undefined), VOCAB);
    expect(reserved.errors).toEqual([]);
    expect(reserved.requiredPlugins).toEqual([]);
    expect(
      validateDefinition(event('EXAMPLE_BREAK_UP', undefined), VOCAB)
        .requiredPlugins
    ).toEqual(['example_zones']);
  });

  it('warns when several plugins publish an event and no source is named', () => {
    const zones = fixturePluginInfos().find(
      p => p.plugin_id === 'example_zones'
    );
    if (!zones) throw new Error('missing fixture');
    const vocab = buildVocabulary([
      zones,
      pluginInfo(zones, {plugin_id: 'other_zones', output_kinds: []}),
    ]);
    const sequence = (source: string | undefined) =>
      withWhen({
        op: 'SEQUENCE',
        within_bars: 3,
        steps: [{name: 'EXAMPLE_ZONE_FORMED', source}, {name: 'AOI_EXPIRED'}],
      });
    expect(warnings(sequence(undefined), vocab)).toEqual([
      'event_source_implicit@/when/steps/0/source',
    ]);
    const chosen = validateDefinition(sequence('other_zones'), vocab);
    expect(chosen.warnings).toEqual([]);
    expect(chosen.requiredPlugins).toContain('other_zones');
  });

  it('requires sequence windows to fit the steps', () => {
    const def = withWhen({
      op: 'SEQUENCE',
      within_bars: 1,
      steps: [{name: 'EXAMPLE_ZONE_FORMED'}, {name: 'EXAMPLE_BREAK_UP'}],
    });
    expect(codes(def, VOCAB)).toEqual(['window_too_small']);
  });

  it('checks time windows', () => {
    const window = (patch: object) =>
      withWhen({
        op: 'TIME_WINDOW',
        time_zone: 'America/New_York',
        windows: [{start: '09:30', end: '11:30'}],
        ...patch,
      });
    expect(codes(window({}), undefined)).toEqual([]);
    expect(codes(window({time_zone: 'Mars/Olympus'}), undefined)).toEqual([
      'time_zone_unknown',
    ]);
    expect(
      codes(window({windows: [{start: '9:30', end: '11:30'}]}), undefined)
    ).toEqual(['pattern']);
    expect(
      codes(window({windows: [{start: '10:00', end: '10:00'}]}), undefined)
    ).toEqual(['time_window_empty']);
    expect(codes(window({windows: []}), undefined)).toEqual(['minItems']);
    expect(codes(window({weekdays: [1, 1]}), undefined)).toEqual([
      'uniqueItems',
    ]);
    expect(codes(window({weekdays: [0]}), undefined)).toEqual(['items']);
  });

  it('checks the nearest-AOI target', () => {
    const risk = (patch: Partial<RiskModel>): StrategyDefinition => ({
      ...template(2),
      risk: {...template(2).risk, ...patch},
    });
    expect(codes(risk({target_kinds: undefined}), VOCAB)).toEqual([
      'target_kinds_required',
    ]);
    expect(
      validateDefinition(risk({target_kinds: ['NOPE']}), VOCAB).errors.map(
        e => e.path
      )
    ).toEqual(['/risk/target_kinds/0']);
    expect(
      warnings(risk({target_model: 'FIXED_R', fixed_r: '2'}), VOCAB)
    ).toEqual(['target_kinds_ignored@/risk/target_kinds']);
  });

  it('flags unknown indicator plugins and fields in operands', () => {
    const def = withWhen({
      op: 'COMPARE',
      left: {kind: 'indicator', plugin_id: 'rsi', field: 'nope'},
      cmp: 'gt',
      right: {kind: 'indicator', plugin_id: 'missing', field: 'x'},
    });
    expect(codes(def, VOCAB)).toEqual(['plugin_unknown']);
    expect(warnings(def, VOCAB)).toEqual([
      'unknown_indicator_field@/when/left/field',
    ]);
  });

  it('rejects unsupported nodes instead of skipping them', () => {
    const def = withWhen({op: 'ALL', children: [{op: 'RSI_ABOVE', value: 70}]});
    expect(validateDefinition(def).errors[0]).toMatchObject({
      path: '/when/children/0/op',
      code: 'unsupported_node',
    });
  });

  it('rejects unknown fields and duplicate ids', () => {
    const def = withWhen({
      op: 'ALL',
      children: [
        {op: 'RISK_REWARD', id: 'x', min_rr: '2', extra: 1},
        {op: 'RISK_REWARD', id: 'x', min_rr: '2'},
      ],
    });
    expect(codes(def, undefined)).toEqual(
      expect.arrayContaining(['additionalProperties', 'duplicate_id'])
    );
  });

  it('accepts every contract venue and rejects unknown ones', () => {
    const def = template(2);
    expect(validateDefinition(def).valid).toBe(true);
    const bad = {...def, universe: {...def.universe, venues: ['nowhere']}};
    expect(codes(bad, undefined)).toEqual(['enum']);
  });

  it('warns about AUTO_LIVE without blocking', () => {
    const def = {...template(0), execution_mode: 'AUTO_LIVE'};
    const result = validateDefinition(def);
    expect(result.valid).toBe(true);
    expect(result.warnings.map(w => w.code)).toContain('auto_live');
    for (const warning of result.warnings) {
      expect(WARNING_CODES.has(warning.code)).toBe(true);
    }
  });
});

describe('nodeIdAtPointer', () => {
  it('maps a server error pointer to the deepest node id', () => {
    const def = template(0);
    expect(nodeIdAtPointer(def, '/when/children/1/left/field')).toBe('htf');
    expect(nodeIdAtPointer(def, '/when')).toBe('root');
    expect(nodeIdAtPointer(def, '/risk/min_rr')).toBeNull();
  });
});
