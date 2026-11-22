/**
 * @fileoverview Production dependency license allowlist check, plus a
 * guard that the Lightweight Charts attribution is shipped.
 */
import {execFileSync} from 'node:child_process';
import {existsSync, readFileSync} from 'node:fs';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = join(fileURLToPath(new URL('.', import.meta.url)), '..');
const ALLOWED = new Set([
  'MIT',
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
 * Reviewed exceptions: server-side only, never shipped in the browser bundle.
 * Each entry needs a written justification.
 */
const EXCEPTIONS = [
  {
    prefix: '@img/sharp-libvips-',
    reason:
      'Optional native image optimizer pulled in by Next.js for server-side next/image; dynamically linked LGPL library, not bundled into client code.',
  },
];

/**
 * Whether an SPDX expression is acceptable (every OR-branch alternative or
 * every AND-term must be allowed).
 * @param {string} expression SPDX license expression.
 * @return {boolean} True when allowed.
 */
function allowed(expression) {
  const clean = expression.replace(/[()]/g, '').trim();
  if (ALLOWED.has(clean)) return true;
  if (clean.includes(' OR '))
    return clean.split(' OR ').some(part => allowed(part));
  if (clean.includes(' AND '))
    return clean.split(' AND ').every(part => allowed(part));
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
    const exception = EXCEPTIONS.find(e => pkg.name.startsWith(e.prefix));
    if (exception && !allowed(license)) {
      console.log(`Exception: ${pkg.name} (${license}): ${exception.reason}`);
      continue;
    }
    if (!allowed(license))
      problems.push(
        `${pkg.name}@${pkg.versions?.join(',') ?? '?'}: ${license}`
      );
  }
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
  for (const problem of problems) console.error(`  ${problem}`);
  process.exit(1);
}
console.log(
  `License check passed: ${count} production packages, all on the allowlist; attribution present.`
);
