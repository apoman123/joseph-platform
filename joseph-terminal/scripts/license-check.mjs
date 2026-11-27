/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview License check for the terminal's production dependencies
 * (`pnpm licenses:check`, run in CI).
 *
 * Every package that `pnpm licenses list --prod` reports must have a license
 * on `ALLOWED` or match a reviewed entry in `EXCEPTIONS`, and both
 * docs/THIRD_PARTY_NOTICES.md and NOTICE must carry the TradingView
 * Lightweight Charts attribution that its Apache-2.0 license requires. Exits
 * with status 1 and lists each problem otherwise.
 */

import {execFileSync} from 'node:child_process';
import {existsSync, readFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

/** The joseph-terminal directory, the parent of this script's folder. */
const ROOT = join(fileURLToPath(new URL('.', import.meta.url)), '..');

/** Reviewed SPDX identifiers for the AGPL integrated application. */
const ALLOWED = new Set([
  'MIT',
  'AGPL-3.0-only',
  'ISC',
  'Apache-2.0',
  'BSD-2-Clause',
  'BSD-3-Clause',
  '0BSD',
  'CC0-1.0',
  'BlueOak-1.0.0',
  'Unlicense',
  'Python-2.0',
  'CC-BY-4.0',
]);

/**
 * Reviewed exceptions for packages whose name starts with `prefix`: they are
 * used on the server only and never shipped in the browser bundle. Each entry
 * carries the justification that the check prints.
 */
const EXCEPTIONS = [
  {
    prefix: '@img/sharp-libvips-',
    reason:
      'Optional native image optimizer pulled in by Next.js for server-side next/image; dynamically linked LGPL library, not bundled into client code.',
  },
];

/**
 * Returns whether an SPDX license expression is acceptable: a single allowed
 * identifier, an `OR` with at least one acceptable alternative, or an `AND`
 * whose terms are all acceptable.
 *
 * Parentheses are dropped and `OR` is split first, so `AND` binds tighter, as
 * in SPDX; a parenthesized `OR` inside an `AND` is not grouped.
 * @param {string} expression SPDX license expression, e.g. `(MIT OR CC0-1.0)`.
 * @return {boolean} True when the expression is acceptable.
 */
function allowed(expression) {
  const clean = expression.replace(/[()]/g, '').trim();
  if (ALLOWED.has(clean)) return true;
  if (clean.includes(' OR ')) {
    return clean.split(' OR ').some(part => allowed(part));
  }
  if (clean.includes(' AND ')) {
    return clean.split(' AND ').every(part => allowed(part));
  }
  return false;
}

const raw = execFileSync('pnpm', ['licenses', 'list', '--json', '--prod'], {
  cwd: ROOT,
  encoding: 'utf8',
});
const byLicense = JSON.parse(raw);
const problems = [];
let count = 0;
for (const [license, packages] of Object.entries(byLicense)) {
  for (const pkg of packages) {
    count += 1;
    if (pkg.name === 'pinets' || pkg.name === '@luxalgo/vela-pinets') {
      problems.push(
        `${pkg.name}: Joseph uses Python scripts; Pine runtimes must not ship`
      );
    }
    const exception = EXCEPTIONS.find(e => pkg.name.startsWith(e.prefix));
    if (exception && !allowed(license)) {
      console.log(`Exception: ${pkg.name} (${license}): ${exception.reason}`);
      continue;
    }
    if (!allowed(license)) {
      problems.push(
        `${pkg.name}@${pkg.versions?.join(',') ?? '?'}: ${license}`
      );
    }
  }
}

const aggregateLicense = readFileSync(join(ROOT, 'LICENSE'), 'utf8');
if (!aggregateLicense.includes('GNU AFFERO GENERAL PUBLIC LICENSE')) {
  problems.push(
    'The TradeX integration requires the approved AGPL-3.0 license'
  );
}
if (
  !readFileSync(join(ROOT, 'NOTICE'), 'utf8').includes(
    'Based on TradeX by Saifullah Shafin'
  )
) {
  problems.push('NOTICE must preserve the upstream TradeX attribution');
}

const notices = join(ROOT, 'docs', 'THIRD_PARTY_NOTICES.md');
if (
  !existsSync(notices) ||
  !/lightweight-charts/i.test(readFileSync(notices, 'utf8'))
) {
  problems.push(
    'docs/THIRD_PARTY_NOTICES.md must credit lightweight-charts (Apache-2.0)'
  );
}
if (
  !existsSync(join(ROOT, 'NOTICE')) ||
  !/TradingView/.test(readFileSync(join(ROOT, 'NOTICE'), 'utf8'))
) {
  problems.push(
    'NOTICE must contain the TradingView Lightweight Charts attribution'
  );
}

if (problems.length > 0) {
  console.error('License check failed:');
  for (const problem of problems) {
    console.error(`  ${problem}`);
  }
  process.exit(1);
}
console.log(
  `License check passed: ${count} production packages, all on the allowlist; attribution present.`
);
