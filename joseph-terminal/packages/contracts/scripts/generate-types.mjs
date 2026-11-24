/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Generates TypeScript types from the canonical JSON Schemas.
 *
 * All schema files are bundled into one document whose `$defs` hold every
 * definition (names must be unique across files), so each contract type is
 * emitted exactly once. Run with `--check` in CI to fail on drift.
 */

import {readFileSync, readdirSync, writeFileSync, mkdirSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {compile} from 'json-schema-to-typescript';

const PACKAGE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCHEMA_DIR = join(PACKAGE_DIR, 'schemas');
const OUTPUT = join(PACKAGE_DIR, 'src', 'generated', 'contracts.ts');
const BANNER = `/**
 * @fileoverview GENERATED FILE - DO NOT EDIT.
 * Source: packages/contracts/schemas/*.schema.json
 * Regenerate with \`pnpm contracts:gen\`.
 */
/* eslint-disable */`;

/**
 * Rewrites cross-file `$ref`s to local `#/$defs/...` references and drops
 * keywords that sit next to a `$ref`.
 * @param {unknown} node Schema node to rewrite in place.
 * @return {unknown} The rewritten node.
 */
function localizeRefs(node) {
  if (Array.isArray(node)) return node.map(localizeRefs);
  if (node && typeof node === 'object') {
    if (typeof node.$ref === 'string') {
      // Sibling keywords next to $ref make the generator emit duplicate
      // aliases (Timeframe1, ...); documentation stays in the schema files.
      return {$ref: '#/$defs/' + node.$ref.split('#/$defs/')[1]};
    }
    const out = {};
    for (const [key, value] of Object.entries(node)) {
      if (key === '$ref' && typeof value === 'string') {
        out[key] = '#/$defs/' + value.split('#/$defs/')[1];
      } else {
        out[key] = localizeRefs(value);
      }
    }
    return out;
  }
  return node;
}

/**
 * Bundles every schema file into a single document.
 * @return {object} Bundled JSON Schema.
 */
function bundle() {
  const defs = {};
  const files = readdirSync(SCHEMA_DIR)
    .filter(f => f.endsWith('.schema.json'))
    .sort();
  for (const file of files) {
    const schema = JSON.parse(readFileSync(join(SCHEMA_DIR, file), 'utf8'));
    for (const [name, def] of Object.entries(schema.$defs ?? {})) {
      if (defs[name]) {
        throw new Error(`Duplicate definition ${name} in ${file}`);
      }
      defs[name] = localizeRefs(def);
    }
  }
  return {
    $schema: 'https://json-schema.org/draft/2020-12/schema',
    title: 'JosephContracts',
    type: 'object',
    additionalProperties: false,
    $defs: defs,
  };
}

/**
 * Generates the TypeScript source and writes or checks it.
 * @return {Promise<void>} Resolves when done.
 */
async function main() {
  const raw = await compile(bundle(), 'JosephContracts', {
    bannerComment: BANNER,
    unreachableDefinitions: true,
    additionalProperties: false,
    declareExternallyReferenced: true,
    strictIndexSignatures: true,
    style: {
      singleQuote: true,
      bracketSpacing: false,
      trailingComma: 'es5',
      printWidth: 80,
    },
  });
  const source = raw
    .replace(
      /\/\*\*\n \* This interface was referenced by `JosephContracts`'s JSON-Schema\n \* via the `definition` "JosephContracts"\.\n \*\/\nexport interface JosephContracts \{\}\n/,
      ''
    )
    .replace(/export interface JosephContracts \{\}\n/, '');
  const check = process.argv.includes('--check');
  if (check) {
    const current = readFileSync(OUTPUT, 'utf8');
    if (current !== source) {
      console.error(
        'Generated contracts are out of date. Run `pnpm contracts:gen`.'
      );
      process.exit(1);
    }
    console.log('Generated contracts are up to date.');
    return;
  }
  mkdirSync(dirname(OUTPUT), {recursive: true});
  writeFileSync(OUTPUT, source);
  console.log(`Wrote ${OUTPUT}`);
}

await main();
