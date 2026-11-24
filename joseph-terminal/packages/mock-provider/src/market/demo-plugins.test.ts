/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview The DEMO plugin descriptors against the contract: schema
 * validity, the shared plugin fixtures and the engine's export of its
 * built-in plugins.
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import type {IndicatorPluginInfo} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {demoPluginInfos} from './demo-plugins';

const CONTRACTS = join(__dirname, '..', '..', '..', 'contracts');
const SCHEMA_DIR = join(CONTRACTS, 'schemas');
const BASE = 'https://schemas.joseph.local/v1/';
const FIXTURE = join(CONTRACTS, 'fixtures', 'indicator-plugin.fixture.json');
/** Engine export written by `scripts/export_openapi.py`. */
const ARTIFACT = join(CONTRACTS, 'openapi', 'builtin-indicator-plugins.json');

/**
 * Builds a validator for one contract definition.
 * @param def Definition name in `indicators.schema.json`.
 * @return Validation function.
 * @throws Error when the schema does not define `def`.
 */
function validator(def: string) {
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const file of readdirSync(SCHEMA_DIR)) {
    if (file.endsWith('.schema.json')) {
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMA_DIR, file), 'utf8')));
    }
  }
  const validate = ajv.getSchema(`${BASE}indicators.schema.json#/$defs/${def}`);
  if (!validate) throw new Error(`missing ${def}`);
  return validate;
}

/**
 * Drops the fields a mirror may word or version differently.
 * @param info Plugin descriptor.
 * @return Copy without `description` and `indicator_version`.
 */
function comparable(info: IndicatorPluginInfo): Record<string, unknown> {
  const copy: Record<string, unknown> = structuredClone({...info});
  delete copy.description;
  delete copy.indicator_version;
  return copy;
}

describe('DEMO plugin descriptors', () => {
  const infos = demoPluginInfos();

  it('mirror the nine built-ins in load order', () => {
    expect(infos.map(info => info.plugin_id)).toEqual([
      'sma',
      'ema',
      'anchored_vwap',
      'regression_channel',
      'bollinger_bands',
      'rsi',
      'atr',
      'volume_delta',
      'pivot_points',
    ]);
  });

  it('validate against the contract', () => {
    const validate = validator('IndicatorPluginList');
    expect(validate({plugins: infos})).toBe(true);
    expect(validate.errors ?? []).toEqual([]);
  });

  it('declare every plotted value and pane', () => {
    for (const info of infos) {
      const keys = new Set(info.output_values.map(value => value.key));
      const panes = new Set(info.display.panes.map(pane => pane.id));
      for (const plot of info.display.plots) {
        expect(keys.has(plot.value)).toBe(true);
        expect(plot.pane === 'price' || panes.has(plot.pane)).toBe(true);
      }
      for (const style of info.display.kinds) {
        expect(info.output_kinds).toContain(style.kind);
      }
    }
  });

  it('equal the shared plugin fixtures', () => {
    const fixture = JSON.parse(readFileSync(FIXTURE, 'utf8')) as {
      valid: IndicatorPluginInfo[];
    };
    const byId = new Map(infos.map(info => [info.plugin_id, info]));
    const builtins = fixture.valid.filter(info => byId.has(info.plugin_id));
    expect(builtins.map(info => info.plugin_id)).toEqual([
      'ema',
      'rsi',
      'volume_delta',
      'pivot_points',
    ]);
    for (const expected of builtins) {
      const actual = byId.get(expected.plugin_id);
      if (!actual) throw new Error(`missing ${expected.plugin_id}`);
      expect(comparable(actual)).toEqual(comparable(expected));
    }
  });

  it('bound the delta coverage for the settings dialog', () => {
    const delta = infos.find(info => info.plugin_id === 'volume_delta');
    const properties = (
      delta?.input_schema as {properties: Record<string, unknown>}
    ).properties;
    expect(properties.min_coverage).toMatchObject({
      default: '0.8',
      minimum: '0.5',
      maximum: '1',
    });
  });

  it('hand out fresh copies', () => {
    const first = demoPluginInfos()[1];
    if (first) first.display.plots.length = 0;
    expect(demoPluginInfos()[1]?.display.plots).toHaveLength(1);
  });
});

describe('DEMO plugin descriptors against the engine export (scripts/export_openapi.py)', () => {
  it('equal the engine built-ins, ignoring description and version', () => {
    const raw: unknown = JSON.parse(readFileSync(ARTIFACT, 'utf8'));
    // The export is either the plugin list or the bare array.
    const plugins = (
      Array.isArray(raw) ? raw : (raw as {plugins: unknown[]}).plugins
    ) as IndicatorPluginInfo[];
    expect(demoPluginInfos().map(comparable)).toEqual(plugins.map(comparable));
  });
});
