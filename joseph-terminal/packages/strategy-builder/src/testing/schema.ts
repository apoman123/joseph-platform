/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Test helper validating definitions against the canonical
 * JSON Schema with ajv.
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';

const SCHEMA_DIR = join(__dirname, '..', '..', '..', 'contracts', 'schemas');
const BASE = 'https://schemas.joseph.local/v1/';

/**
 * Builds an ajv instance with every contract schema registered.
 * @return Ajv instance.
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

const AJV = buildAjv();

/**
 * Validates a value against a contract definition.
 * @param schemaFile Schema file name, e.g. `strategy.schema.json`.
 * @param definition Definition name, e.g. `StrategyDefinition`.
 * @param value Value to validate.
 * @return Error messages (empty when valid).
 */
export function schemaErrors(
  schemaFile: string,
  definition: string,
  value: unknown
): string[] {
  const validate = AJV.getSchema(`${BASE}${schemaFile}#/$defs/${definition}`);
  if (!validate) throw new Error(`Unknown definition ${definition}`);
  return validate(value)
    ? []
    : (validate.errors ?? []).map(e => `${e.instancePath} ${e.message ?? ''}`);
}

/**
 * Reads a shared contract fixture file.
 * @param name Fixture base name, e.g. `strategy`.
 * @return Parsed fixture with valid and invalid examples.
 */
export function readFixture(name: string): {
  valid: unknown[];
  invalid: unknown[];
} {
  const path = join(
    __dirname,
    '..',
    '..',
    '..',
    'contracts',
    'fixtures',
    `${name}.fixture.json`
  );
  return JSON.parse(readFileSync(path, 'utf8')) as {
    valid: unknown[];
    invalid: unknown[];
  };
}
