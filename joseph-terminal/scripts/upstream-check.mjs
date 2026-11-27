/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Verifies the reviewed, immutable upstream source snapshot. */

import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {resolve, relative, isAbsolute} from 'node:path';

const root = fileURLToPath(new URL('../third_party/tradex/', import.meta.url));
const manifest = JSON.parse(
  readFileSync(resolve(root, 'UPSTREAM.json'), 'utf8')
);
if (manifest.commit !== 'f8532c31af5060ee4dbafcce11fef0ea8f984aeb') {
  throw new Error('Upstream revision changed without an integration review');
}
for (const [name, hash] of Object.entries(manifest.files)) {
  const path = resolve(root, name);
  const local = relative(root, path);
  if (local.startsWith('..') || isAbsolute(local)) {
    throw new Error(
      'Upstream manifest contains a path outside its source directory'
    );
  }
  const actual = createHash('sha256').update(readFileSync(path)).digest('hex');
  if (actual !== hash) throw new Error(`Upstream source changed: ${name}`);
}
console.log(
  `Verified ${Object.keys(manifest.files).length} upstream files at ${manifest.commit}`
);
