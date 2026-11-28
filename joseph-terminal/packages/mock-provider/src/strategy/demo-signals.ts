/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO signal fixtures shaped exactly like backend output.
 * They are static examples for offline UI work, not computed signals. The
 * signal bar's base volume and quote turnover come from the DEMO venue's
 * candle for that bar (as the engine copies them from the venue candle).
 */

import type {ReasonNode, Signal} from '@joseph/contracts';
import {DEMO_INSTRUMENT_SPECS, demoCandle} from '../market/prices';

/**
 * Builds a reason node.
 * @param path JSON Pointer of the node in the definition, e.g.
 *     `/when/children/0`.
 * @param op Condition operator, e.g. `ALL` or `STATE`.
 * @param result Tri-state result.
 * @param detail Human-readable explanation.
 * @param children Child reasons (default none).
 * @return Reason node without evidence.
 */
function reason(
  path: string,
  op: string,
  result: ReasonNode['result'],
  detail: string,
  children: ReasonNode[] = []
): ReasonNode {
  return {
    node_path: path,
    node_id: null,
    op,
    result,
    detail,
    evidence: {},
    children,
  };
}

/**
 * Reads the volume fields of a signal bar from the DEMO venue candle.
 * @param instrumentId DEMO instrument id.
 * @param timeframe Signal timeframe.
 * @param barStartMs Start of the signal bar, epoch ms.
 * @param nowMs Current time in epoch ms.
 * @return Base volume and quote turnover (null for unknown instruments).
 */
function barVolumes(
  instrumentId: string,
  timeframe: Signal['timeframe'],
  barStartMs: number,
  nowMs: number
): Pick<Signal, 'bar_base_volume' | 'bar_quote_volume'> {
  const spec = DEMO_INSTRUMENT_SPECS.find(s => s.instrumentId === instrumentId);
  if (!spec) return {bar_base_volume: null, bar_quote_volume: null};
  const candle = demoCandle(spec, timeframe, barStartMs, nowMs);
  return {
    bar_base_volume: candle.base_volume,
    bar_quote_volume: candle.quote_turnover ?? null,
  };
}

/**
 * Creates DEMO signals relative to a reference time, one per built-in
 * strategy.
 * @param nowMs Reference time in epoch ms (UTC); the first two signals sit
 *     on the 5m bars that closed 0 and 3 bars before the 5-minute boundary
 *     at or before it, the third on the 15m bar that closed 2 bars before
 *     the 15-minute boundary.
 * @return Three signals (fired, blocked, confirming), newest first.
 */
export function createDemoSignals(nowMs: number): Signal[] {
  const barMs = 300_000;
  const close = Math.floor(nowMs / barMs) * barMs;
  const slowBarMs = 900_000;
  const slowClose = Math.floor(nowMs / slowBarMs) * slowBarMs;
  // Placeholder digest: one hex digit repeated, clearly not a real hash.
  const hash = (digit: string) => `sha256:${digit.repeat(64)}`;
  const base = {
    tenant_id: 'demo-tenant',
    evaluator_version: 'demo-2.0.0',
    data_source: 'DEMO' as const,
    revision: 0,
  };
  return [
    {
      ...base,
      signal_id: 'demo-signal-1',
      dedupe_key:
        'demo-tenant|builtin:pivot-bounce-long:vdemo|mock:perp:BTCUSD|LONG|setup-1|ENTRY_CONFIRMED',
      strategy_id: 'builtin:pivot-bounce-long',
      strategy_version_id: 'builtin:pivot-bounce-long:vdemo',
      setup_id: 'setup-1',
      instrument_id: 'mock:perp:BTCUSD',
      direction: 'LONG',
      timeframe: '5m',
      kind: 'ENTRY_CONFIRMED',
      setup_state: 'FIRED',
      severity: 'ENTRY',
      bar_start_ms: close - barMs,
      bar_close_ms: close,
      evaluated_at_ms: close + 120,
      inputs_snapshot_hash: hash('a'),
      rule_version: hash('b'),
      indicator_versions: {pivot_points: 'demo', volume_delta: 'demo'},
      reason_tree: reason('/when', 'ALL', 'TRUE', '4 of 4 conditions true', [
        reason(
          '/when/children/0',
          'AOI_TOUCH',
          'TRUE',
          'Wick touched pivot S1 demo-pivot-s1-5m two bars ago'
        ),
        reason(
          '/when/children/1',
          'TIME_WINDOW',
          'TRUE',
          'Bar starts inside 09:30-11:30 America/New_York on a weekday'
        ),
        reason(
          '/when/children/2',
          'STATE',
          'TRUE',
          'Volume delta method is taker_volume on 5m'
        ),
        reason(
          '/when/children/3',
          'RISK_REWARD',
          'TRUE',
          'Projected 2.21R after costs to the pivot'
        ),
      ]),
      aoi_ids: ['demo-pivot-s1-5m'],
      group_ids: [],
      ...barVolumes('mock:perp:BTCUSD', '5m', close - barMs, nowMs),
      entry_price: '94790',
      stop_price: '94650',
      target_price: '95100',
      rr: '2.21',
      risk_evaluation: {status: 'PASS', reasons: []},
      mode: 'PAPER',
      message: 'DEMO: Pivot bounce (long) fired on mock BTCUSD 5m',
    },
    {
      ...base,
      signal_id: 'demo-signal-2',
      dedupe_key:
        'demo-tenant|builtin:rsi-reversion-short:vdemo|mock:perp:ETHUSD|SHORT|setup-7|ENTRY_BLOCKED',
      strategy_id: 'builtin:rsi-reversion-short',
      strategy_version_id: 'builtin:rsi-reversion-short:vdemo',
      setup_id: 'setup-7',
      instrument_id: 'mock:perp:ETHUSD',
      direction: 'SHORT',
      timeframe: '5m',
      kind: 'ENTRY_BLOCKED',
      setup_state: 'BLOCKED',
      severity: 'WARNING',
      bar_start_ms: close - 4 * barMs,
      bar_close_ms: close - 3 * barMs,
      evaluated_at_ms: close - 3 * barMs + 90,
      inputs_snapshot_hash: hash('c'),
      rule_version: hash('d'),
      indicator_versions: {rsi: 'demo', bollinger_bands: 'demo'},
      reason_tree: reason(
        '/when',
        'ALL',
        'UNKNOWN',
        'Blocked: open interest data is missing',
        [
          reason(
            '/when/children/0',
            'CROSSES',
            'TRUE',
            'RSI crossed below 70 (71.8 to 68.9)'
          ),
          reason(
            '/when/children/1',
            'WITHIN_BARS',
            'TRUE',
            'Close was above the upper Bollinger Band 2 bars ago'
          ),
          reason(
            '/when/children/2',
            'NOT',
            'UNKNOWN',
            'OI change is unknown, so the condition cannot be evaluated',
            [
              reason(
                '/when/children/2/child',
                'COMPARE',
                'UNKNOWN',
                'Open interest snapshot missing'
              ),
            ]
          ),
        ]
      ),
      aoi_ids: [],
      group_ids: [],
      ...barVolumes('mock:perp:ETHUSD', '5m', close - 4 * barMs, nowMs),
      entry_price: null,
      stop_price: null,
      target_price: null,
      rr: null,
      risk_evaluation: {
        status: 'UNKNOWN',
        reasons: ['Open interest unavailable'],
      },
      mode: 'ALERT_ONLY',
      message:
        'DEMO: RSI reversion (short) blocked on mock ETHUSD 5m (missing data)',
    },
    {
      ...base,
      signal_id: 'demo-signal-3',
      dedupe_key:
        'demo-tenant|builtin:ema-trend-long:vdemo|mock:perp:SOLUSD|LONG|setup-3|PRE_SIGNAL',
      strategy_id: 'builtin:ema-trend-long',
      strategy_version_id: 'builtin:ema-trend-long:vdemo',
      setup_id: 'setup-3',
      instrument_id: 'mock:perp:SOLUSD',
      direction: 'LONG',
      timeframe: '15m',
      kind: 'PRE_SIGNAL',
      setup_state: 'CONFIRMING',
      severity: 'INFO',
      bar_start_ms: slowClose - 3 * slowBarMs,
      bar_close_ms: slowClose - 2 * slowBarMs,
      evaluated_at_ms: slowClose - 2 * slowBarMs + 75,
      inputs_snapshot_hash: hash('e'),
      rule_version: hash('f'),
      indicator_versions: {ema: 'demo', rsi: 'demo', volume_delta: 'demo'},
      reason_tree: reason('/when', 'ALL', 'FALSE', '3 of 4 conditions true', [
        reason(
          '/when/children/0',
          'CROSSES',
          'TRUE',
          'Close crossed above the EMA'
        ),
        reason(
          '/when/children/1',
          'COMPARE',
          'FALSE',
          '1h RSI is 48.2, not above 50'
        ),
        reason('/when/children/2', 'COMPARE', 'TRUE', 'Volume delta is +12.4%'),
      ]),
      aoi_ids: [],
      group_ids: [],
      ...barVolumes(
        'mock:perp:SOLUSD',
        '15m',
        slowClose - 3 * slowBarMs,
        nowMs
      ),
      entry_price: null,
      stop_price: null,
      target_price: null,
      rr: null,
      risk_evaluation: {status: 'NOT_APPLICABLE', reasons: []},
      mode: 'PAPER',
      message: 'DEMO: EMA trend (long) setup confirming on mock SOLUSD 15m',
    },
  ];
}
