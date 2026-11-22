/**
 * @fileoverview Repository secret scanner (fast pre-commit/CI guard; run
 * gitleaks in CI as well). Fails on private keys, provider tokens, high
 * entropy secret assignments and committed .env files.
 */
import {readFileSync, readdirSync, statSync} from 'node:fs';
import {join, relative} from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = join(fileURLToPath(new URL('.', import.meta.url)), '..');
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
const SKIP_FILES =
  /\.(png|jpe?g|gif|ico|woff2?|ttf|zip|lock)$|pnpm-lock\.yaml$/;
/** Paths allowed to contain example tokens (documented fakes). */
const ALLOWLIST = [
  /^scripts\/secret-scan\.mjs$/,
  /\.test\.tsx?$/,
  /\/fixtures\//,
];

/** Detection rules. */
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
 * Walks the repository.
 * @param dir Directory.
 * @param out Collected files.
 * @return Files.
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
  if (statSync(file).size > 2_000_000) continue;
  const text = readFileSync(file, 'utf8');
  text.split('\n').forEach((line, index) => {
    for (const rule of RULES) {
      if (rule.re.test(line))
        findings.push(`${rel}:${index + 1}: ${rule.name}`);
    }
  });
}

if (findings.length > 0) {
  console.error(`Secret scan found ${findings.length} problem(s):`);
  for (const finding of findings) console.error(`  ${finding}`);
  process.exit(1);
}
console.log('Secret scan passed: no credentials, keys or .env files found.');
