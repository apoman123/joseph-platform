/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Generates `src/generated/contracts.ts` from the canonical
 * JSON Schemas in `schemas/`.
 *
 * All schema files are bundled into one document whose `$defs` hold every
 * definition (names must be unique across files), so each contract type is
 * emitted exactly once. The output starts with the project licence header.
 * `pnpm contracts:gen` writes the file; `pnpm contracts:check` (the `--check`
 * flag, run in CI) exits with status 1 when the committed file differs from
 * what the schemas produce.
 */

import {readFileSync, readdirSync, writeFileSync, mkdirSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {compile} from 'json-schema-to-typescript';

import {SCRIPT_HEADER} from '../../../scripts/lint/license-header.mjs';

const PACKAGE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..');
const SCHEMA_DIR = join(PACKAGE_DIR, 'schemas');
const OUTPUT = join(PACKAGE_DIR, 'src', 'generated', 'contracts.ts');
// The same licence header every hand-written file carries. The generated
// file is exempt from lint, hence the blanket `eslint-disable`.
const BANNER = `${SCRIPT_HEADER}

/**
 * @fileoverview GENERATED FILE - DO NOT EDIT.
 * Source: packages/contracts/schemas/*.schema.json
 * Regenerate with \`pnpm contracts:gen\`.
 */
/* eslint-disable */`;

/**
 * Returns a copy of a schema node whose cross-file `$ref`s point into the
 * bundle's local `$defs`.
 *
 * A `$ref` has the form `<file>.schema.json#/$defs/<Name>` or
 * `#/$defs/<Name>`. Definition names are unique across files (`bundle` checks
 * this), so the file part can be dropped. An object that holds a `$ref` keeps
 * only the `$ref`.
 * @param {unknown} node Schema node: any JSON value.
 * @return {unknown} A new array or object with the references rewritten, or
 *     `node` itself when it is a primitive.
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
 * Bundles every `*.schema.json` file into a single JSON Schema document.
 * @return {object} A 2020-12 schema whose `$defs` hold the definitions of
 *     every file, with references localized.
 * @throws {Error} When two schema files define the same `$defs` name.
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
 * Generates the TypeScript source, then writes it to `OUTPUT` or, with
 * `--check`, compares it with the committed file and exits with status 1 on
 * any difference.
 * @return {Promise<void>} Resolves once the file is written or found current;
 *     rejects when the schemas cannot be bundled or compiled.
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
  // The bundle's root object type is empty and unused; drop it and its
  // generated comment.
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
