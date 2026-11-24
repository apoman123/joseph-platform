/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Secret scanner for the terminal's working tree (`pnpm
 * secrets:scan`, run in CI).
 *
 * Reports, as `path:line: rule`, every line that matches one of `RULES`
 * (private key blocks, AWS/GitHub/Slack/Telegram tokens, 64-digit hex private
 * keys, and long quoted values assigned to secret-like names such as
 * `api_key`), plus every `.env` file other than `.env.example`, and exits with
 * status 1 if there is any finding. It reads files on disk, not git history,
 * so it is a fast guard that complements a history scanner such as gitleaks.
 */

import {
  closeSync,
  fstatSync,
  openSync,
  readFileSync,
  readdirSync,
  statSync,
} from 'node:fs';
import {join, relative} from 'node:path';
import {fileURLToPath} from 'node:url';

/** The joseph-terminal directory, the parent of this script's folder. */
const ROOT = join(fileURLToPath(new URL('.', import.meta.url)), '..');

/** Directory names never entered: dependencies, build output and reports. */
const SKIP_DIRS = new Set([
  'node_modules',
  '.next',
  '.next-e2e',
  'coverage',
  'playwright-report',
  'test-results',
  '.git',
  '.turbo',
]);

/** Binary assets and lock files, which are not scanned. */
const SKIP_FILES =
  /\.(png|jpe?g|gif|ico|woff2?|ttf|zip|lock)$|pnpm-lock\.yaml$/;

/**
 * Relative paths whose contents are not scanned because they hold fake
 * example tokens: this script's own patterns, unit tests and fixture data.
 * The `.env` check still applies to them.
 */
const ALLOWLIST = [
  /^scripts\/secret-scan\.mjs$/,
  /\.test\.tsx?$/,
  /\/fixtures\//,
];

/**
 * Detection rules, each a named pattern matched against one line at a time.
 * The patterns have no `g` flag, so `test` keeps no state between lines.
 */
const RULES = [
  {
    name: 'private key block',
    re: /-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----/,
  },
  {name: 'AWS access key', re: /\bAKIA[0-9A-Z]{16}\b/},
  {name: 'GitHub token', re: /\bgh[pousr]_[A-Za-z0-9]{36,}\b/},
  {name: 'Slack token', re: /\bxox[abpors]-[A-Za-z0-9-]{10,}\b/},
  {name: 'Telegram bot token', re: /\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b/},
  {name: 'Ethereum-style private key', re: /\b0x[0-9a-fA-F]{64}\b/},
  {
    name: 'secret assignment',
    re: /\b(?:api[_-]?secret|secret[_-]?key|private[_-]?key|api[_-]?key|password|passphrase|token)\b["']?\s*[:=]\s*["'][A-Za-z0-9+/_=-]{32,}["']/i,
  },
];

/**
 * Collects every file under a directory, recursively, skipping `SKIP_DIRS`.
 * @param {string} dir Absolute path of the directory to walk.
 * @param {string[]=} out Array the file paths are appended to; callers omit
 *     it and the recursion passes it down.
 * @return {string[]} `out`, holding the absolute path of every file found.
 */
function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const path = join(dir, name);
    const stat = statSync(path);
    if (stat.isDirectory()) walk(path, out);
    else out.push(path);
  }
  return out;
}

const findings = [];
for (const file of walk(ROOT)) {
  const rel = relative(ROOT, file).replaceAll('\\', '/');
  if (/(^|\/)\.env(\.[^/]*)?$/.test(rel) && !rel.endsWith('.env.example')) {
    findings.push(`${rel}: committed .env file`);
    continue;
  }
  if (SKIP_FILES.test(rel) || ALLOWLIST.some(re => re.test(rel))) continue;
  // Size check and read use one descriptor, so they see the same file.
  const fd = openSync(file, 'r');
  let text;
  try {
    if (fstatSync(fd).size > 2_000_000) continue;
    text = readFileSync(fd, 'utf8');
  } finally {
    closeSync(fd);
  }
  text.split('\n').forEach((line, index) => {
    for (const rule of RULES) {
      if (rule.re.test(line)) {
        findings.push(`${rel}:${index + 1}: ${rule.name}`);
      }
    }
  });
}

if (findings.length > 0) {
  console.error(`Secret scan found ${findings.length} problem(s):`);
  for (const finding of findings) {
    console.error(`  ${finding}`);
  }
  process.exit(1);
}
console.log('Secret scan passed: no credentials, keys or .env files found.');
