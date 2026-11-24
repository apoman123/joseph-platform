/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Consumer-driven contract tests: validates the shared
 * fixtures with ajv (the Python suite validates the same files).
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import {describe, expect, it} from 'vitest';

const ROOT = join(__dirname, '..');
const SCHEMA_DIR = join(ROOT, 'schemas');
const FIXTURE_DIR = join(ROOT, 'fixtures');
const BASE = 'https://schemas.joseph.local/v1/';

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
