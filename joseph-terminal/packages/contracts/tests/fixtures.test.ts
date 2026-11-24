/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Consumer-driven contract tests: validates the shared
 * fixtures with ajv (the Python suite validates the same files), and checks
 * the plugin metadata rules that JSON Schema cannot express.
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {describe, expect, it} from 'vitest';
import type {IndicatorPluginInfo} from '../src/generated/contracts';

const ROOT = join(__dirname, '..');
const SCHEMA_DIR = join(ROOT, 'schemas');
const FIXTURE_DIR = join(ROOT, 'fixtures');
const BASE = 'https://schemas.joseph.local/v1/';
/** Event names any plugin may publish without declaring them. */
const RESERVED_EVENTS = ['AOI_INVALIDATED', 'AOI_EXPIRED'];

/** Shape of a fixture file. */
interface FixtureFile {
  /** Schema file name. */
  schema: string;
  /** Definition name. */
  definition: string;
  /** Instances that must validate. */
  valid: unknown[];
  /** Instances that must be rejected. */
  invalid: unknown[];
}

/**
 * Builds an ajv instance with every schema registered.
 * @return Configured ajv.
 */
function buildAjv(): Ajv2020 {
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const file of readdirSync(SCHEMA_DIR)) {
    if (file.endsWith('.schema.json')) {
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMA_DIR, file), 'utf8')));
    }
  }
  return ajv;
}

const ajv = buildAjv();
const fixtures = readdirSync(FIXTURE_DIR)
  .filter(f => f.endsWith('.fixture.json'))
  .map(f => ({
    name: f,
    body: JSON.parse(readFileSync(join(FIXTURE_DIR, f), 'utf8')) as FixtureFile,
  }));

describe('shared contract fixtures', () => {
  it('finds fixtures', () => {
    expect(fixtures.length).toBeGreaterThan(5);
  });

  for (const {name, body} of fixtures) {
    const validate = ajv.getSchema(
      `${BASE}${body.schema}#/$defs/${body.definition}`
    );
    it(`${name}: valid examples conform`, () => {
      expect(validate).toBeDefined();
      for (const instance of body.valid) {
        const ok = validate?.(instance);
        expect(validate?.errors ?? []).toEqual([]);
        expect(ok).toBe(true);
      }
    });
    it(`${name}: invalid examples are rejected`, () => {
      for (const instance of body.invalid) {
        expect(validate?.(instance)).toBe(false);
      }
    });
  }
});

describe('indicator plugin fixture', () => {
  const body = fixtures.find(
    f => f.name === 'indicator-plugin.fixture.json'
  )?.body;
  const infos = (body?.valid ?? []) as IndicatorPluginInfo[];

  it('holds plugin infos', () => {
    expect(body?.definition).toBe('IndicatorPluginInfo');
    expect(infos.length).toBeGreaterThan(0);
  });

  for (const info of infos) {
    it(`${info.plugin_id}: display refers only to declared outputs`, () => {
      const {display} = info;
      const values = info.output_values.map(v => v.key);
      const panes = display.panes.map(p => p.id);
      const events = [...info.output_events, ...RESERVED_EVENTS];
      for (const style of display.kinds) {
        expect(info.output_kinds).toContain(style.kind);
      }
      for (const style of display.events) {
        expect(events).toContain(style.name);
      }
      for (const plot of display.plots) {
        expect(values).toContain(plot.value);
        expect(['price', ...panes]).toContain(plot.pane);
      }
    });

    it(`${info.plugin_id}: keys, kinds and pane ids are unique`, () => {
      const lists = [
        info.output_kinds,
        info.output_events,
        info.output_values.map(v => v.key),
        info.display.kinds.map(s => s.kind),
        info.display.events.map(s => s.name),
        info.display.panes.map(p => p.id),
        info.display.end_reasons.map(r => r.reason),
      ];
      for (const list of lists) {
        expect(new Set(list).size).toBe(list.length);
      }
    });
  }

  it('declares every AOI kind once across plugins', () => {
    const kinds = infos.flatMap(info => info.output_kinds);
    expect(new Set(kinds).size).toBe(kinds.length);
  });
});
