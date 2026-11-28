/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Parity of the DEMO study evaluator with the Python
 * built-in indicator scripts, on the fixed bars the engine's OpenAPI
 * export runs them over (`contracts/fixtures/builtin-indicator-outputs.json`).
 */

import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {
  CONTINUOUS_CALENDAR,
  type CatalogList,
  type Timeframe,
} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  DEMO_STUDY_IDS,
  evaluateBuiltin,
  formatG6,
  studyPeriods,
  type StudyBar,
} from './demo-scripts';

/** The contracts package directory. */
const CONTRACTS = join(__dirname, '..', '..', '..', 'contracts');

/** One exported Python run. */
interface ParityCase {
  /** Catalog id. */
  script_id: string;
  /** Input overrides. */
  inputs: Record<string, unknown>;
  /** Python plots. */
  plots: Array<{
    name: string;
    points: Array<{time_ms: number; value: number | null; color?: string}>;
  }>;
  /** Python plot styles. */
  plot_styles: Record<string, unknown>;
  /** Python drawings. */
  visuals: unknown[];
  /** Python alerts. */
  alerts: unknown[];
}

/** The exported fixture. */
interface Parity {
  /** Chart timeframe. */
  timeframe: Timeframe;
  /** Chart bars. */
  bars: StudyBar[];
  /** 1m intrabars of the second half. */
  intrabars: StudyBar[];
  /** Completed UTC days. */
  days: StudyBar[];
  /** Runs. */
  cases: ParityCase[];
}

const PARITY = JSON.parse(
  readFileSync(
    join(CONTRACTS, 'fixtures', 'builtin-indicator-outputs.json'),
    'utf8'
  )
) as Parity;

const CATALOG = JSON.parse(
  readFileSync(join(CONTRACTS, 'openapi', 'builtin-scripts.json'), 'utf8')
) as CatalogList;

/**
 * Replaces numbers by values rounded to ten significant digits.
 * @param value JSON value.
 * @return Rounded copy.
 */
function rounded(value: unknown): unknown {
  if (typeof value === 'number') return Number(value.toPrecision(10));
  if (Array.isArray(value)) return value.map(rounded);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, rounded(item)])
    );
  }
  return value;
}

/**
 * Runs one case through the DEMO evaluator.
 * @param parityCase Exported case.
 * @return DEMO output, rounded like the export.
 */
function demoRun(parityCase: ParityCase): unknown {
  const script = CATALOG.scripts.find(s => s.script_id === parityCase.script_id);
  const defaults = Object.fromEntries(
    Object.entries(script?.inputs ?? {}).map(([key, spec]) => [
      key,
      spec?.default,
    ])
  );
  const periods = Object.fromEntries(
    studyPeriods(parityCase.script_id).map(tf => [
      tf,
      PARITY.bars.map(bar => {
        const start = CONTINUOUS_CALENDAR.floor(tf as Timeframe, bar.time_ms);
        return [start, CONTINUOUS_CALENDAR.shift(tf as Timeframe, start, 1)];
      }),
    ])
  ) as Record<string, Array<[number, number]>>;
  const output = evaluateBuiltin(parityCase.script_id, {
    bars: PARITY.bars,
    inputs: {...defaults, ...parityCase.inputs},
    timeframe: PARITY.timeframe,
    extra: {intrabars: PARITY.intrabars, period: PARITY.days},
    periods,
  });
  return rounded(output);
}

describe('DEMO study evaluator', () => {
  it('serves every built-in indicator of the engine catalog', () => {
    expect(
      CATALOG.scripts
        .filter(script => script.kind === 'indicator')
        .map(script => script.script_id)
    ).toEqual(DEMO_STUDY_IDS);
  });

  it.each(PARITY.cases.map(item => [item.script_id, item] as const))(
    'matches the Python built-in %s',
    (_id, parityCase) => {
      const expected = rounded({
        plots: parityCase.plots,
        plot_styles: parityCase.plot_styles,
        visuals: parityCase.visuals,
        alerts: parityCase.alerts,
      });
      expect(demoRun(parityCase)).toEqual(expected);
    }
  );

  it('formats numbers like Python', () => {
    expect(formatG6(46)).toBe('46');
    expect(formatG6(101.25)).toBe('101.25');
    expect(formatG6(0.000012345)).toBe('1.2345e-05');
    expect(formatG6(12_345_678)).toBe('1.23457e+07');
    expect(formatG6(1.0000004)).toBe('1');
  });

  it('refuses scripts DEMO cannot run', () => {
    expect(() =>
      evaluateBuiltin('plugin:x:y', {
        bars: [],
        inputs: {},
        timeframe: '1m',
        extra: {},
        periods: {},
      })
    ).toThrow(/Connect a backend/);
  });
});
