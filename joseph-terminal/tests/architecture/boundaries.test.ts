/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Architecture tests: dependency rules, render-only
 * indicator visualization and the feature manifest graph.
 */

import {spawnSync} from 'node:child_process';
import {readFileSync, readdirSync, statSync} from 'node:fs';
import {join, relative} from 'node:path';
import {describe, expect, it} from 'vitest';

const ROOT = join(__dirname, '..', '..');

/**
 * Lists source files under a directory.
 * @param dir Directory.
 * @return Files.
 */
function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name.startsWith('.next')) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...sources(path));
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(path);
    }
  }
  return out;
}

describe('module boundaries', () => {
  it('dependency-cruiser finds no violations', () => {
    const result = spawnSync(
      join(ROOT, 'node_modules', '.bin', 'depcruise'),
      [
        '--config',
        '.dependency-cruiser.cjs',
        '--output-type',
        'err',
        'apps',
        'packages',
      ],
      {cwd: ROOT, encoding: 'utf8'}
    );
    expect(result.stdout + result.stderr).not.toMatch(/\berror\b/);
    expect(result.status).toBe(0);
  }, 120_000);

  it('only the LWC adapter imports lightweight-charts', () => {
    const offenders = [
      ...sources(join(ROOT, 'packages')),
      ...sources(join(ROOT, 'apps', 'web', 'src')),
    ]
      .filter(file =>
        /from 'lightweight-charts'/.test(readFileSync(file, 'utf8'))
      )
      .map(file => relative(ROOT, file));
    expect(offenders).toEqual([
      'packages/chart-core/src/adapters/lwc/lwc-chart-port.ts',
    ]);
  });

  it('indicator-viz never computes from candles (render-only)', () => {
    const offenders = sources(
      join(ROOT, 'packages', 'indicator-viz', 'src')
    ).filter(file =>
      /\b(Candle|CandleBatch|getCandles|ChartBar)\b/.test(
        readFileSync(file, 'utf8')
      )
    );
    expect(offenders).toEqual([]);
  });

  it('no browser source references venue signing or secrets APIs', () => {
    const offenders = [
      ...sources(join(ROOT, 'packages')),
      ...sources(join(ROOT, 'apps', 'web', 'src')),
    ].filter(file =>
      /(api[_-]?secret|signTypedData|privateKey|HMAC_SHA256|createHmac)/i.test(
        readFileSync(file, 'utf8')
      )
    );
    expect(offenders.map(file => relative(ROOT, file))).toEqual([]);
  });
});
