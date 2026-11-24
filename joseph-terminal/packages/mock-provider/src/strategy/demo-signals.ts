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
 * @param op Condition operator, e.g. `ALL` or `BIAS`.
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
 * @param barStartMs Start of the 5m signal bar, epoch ms.
 * @param nowMs Current time in epoch ms.
 * @return Base volume and quote turnover (null for unknown instruments).
 */
function barVolumes(
  instrumentId: string,
  barStartMs: number,
  nowMs: number
): Pick<Signal, 'bar_base_volume' | 'bar_quote_volume'> {
  const spec = DEMO_INSTRUMENT_SPECS.find(s => s.instrumentId === instrumentId);
  if (!spec) return {bar_base_volume: null, bar_quote_volume: null};
  const candle = demoCandle(spec, '5m', barStartMs, nowMs);
  return {
    bar_base_volume: candle.base_volume,
    bar_quote_volume: candle.quote_turnover ?? null,
  };
}

/**
 * Creates DEMO signals relative to a reference time.
 * @param nowMs Reference time in epoch ms (UTC); the signals sit on the 5m
 *     bars that closed 0, 3 and 6 bars before the 5-minute boundary at or
 *     before it.
 * @return Three signals (fired, blocked, confirming), newest first.
 */
export function createDemoSignals(nowMs: number): Signal[] {
  const barMs = 300_000;
  const close = Math.floor(nowMs / barMs) * barMs;
  // Placeholder digest: one hex digit repeated, clearly not a real hash.
  const hash = (digit: string) => `sha256:${digit.repeat(64)}`;
  const base = {
    tenant_id: 'demo-tenant',
    evaluator_version: 'demo-1.0.0',
    indicator_versions: {
      daily_bias: 'demo',
      fvg_ifvg: 'demo',
      liquidity_sweeps: 'demo',
      order_flow_pressure: 'demo',
      premium_discount_ote: 'demo',
    },
    data_source: 'DEMO' as const,
    revision: 0,
  };
  return [
    {
      ...base,
      signal_id: 'demo-signal-1',
      dedupe_key:
        'demo-tenant|discount-ote-reversal:v1|mock:perp:BTCUSD|LONG|setup-1|ENTRY_CONFIRMED',
      strategy_id: 'discount-ote-reversal',
      strategy_version_id: 'discount-ote-reversal:v1',
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
      reason_tree: reason('/when', 'ALL', 'TRUE', '6 of 6 conditions true', [
        reason('/when/children/0', 'BIAS', 'TRUE', '4h trend is bullish'),
        reason(
          '/when/children/1',
          'PRICE_IN_ZONE',
          'TRUE',
          'Close 94,790 is inside the 1h discount of range demo-r1'
        ),
        reason(
          '/when/children/2',
          'PRICE_IN_ZONE',
          'TRUE',
          'Close is inside the 1h OTE of the same range'
        ),
        reason(
          '/when/children/3',
          'AOI_TOUCH',
          'TRUE',
          'Wick touched bullish FVG demo-fvg-1 two bars ago'
        ),
        reason(
          '/when/children/4',
          'SEQUENCE',
          'TRUE',
          'SSL sweep 3 bars ago, seller failure on this bar'
        ),
        reason(
          '/when/children/5',
          'RISK_REWARD',
          'TRUE',
          'Projected 2.21R after costs'
        ),
      ]),
      aoi_ids: ['demo-fvg-1'],
      range_ids: ['demo-r1'],
      ...barVolumes('mock:perp:BTCUSD', close - barMs, nowMs),
      entry_price: '94790',
      stop_price: '94650',
      target_price: '95100',
      rr: '2.21',
      risk_evaluation: {status: 'PASS', reasons: []},
      mode: 'PAPER',
      message: 'DEMO: Long A fired on mock BTCUSD 5m',
    },
    {
      ...base,
      signal_id: 'demo-signal-2',
      dedupe_key:
        'demo-tenant|premium-ote-short:v1|mock:perp:ETHUSD|SHORT|setup-7|ENTRY_BLOCKED',
      strategy_id: 'premium-ote-short',
      strategy_version_id: 'premium-ote-short:v1',
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
      reason_tree: reason(
        '/when',
        'ALL',
        'UNKNOWN',
        'Blocked: open interest data is missing',
        [
          reason(
            '/when/children/0',
            'BIAS',
            'TRUE',
            '2 of 3 timeframes bearish (1d, 4h)'
          ),
          reason(
            '/when/children/4',
            'SEQUENCE',
            'TRUE',
            'BSL sweep then buyer failure'
          ),
          reason(
            '/when/children/5',
            'NOT',
            'UNKNOWN',
            'OI change is unknown, so the condition cannot be evaluated',
            [
              reason(
                '/when/children/5/child',
                'COMPARE',
                'UNKNOWN',
                'Open interest snapshot missing'
              ),
            ]
          ),
        ]
      ),
      aoi_ids: ['demo-brk-4'],
      range_ids: ['demo-r2'],
      ...barVolumes('mock:perp:ETHUSD', close - 4 * barMs, nowMs),
      entry_price: null,
      stop_price: null,
      target_price: null,
      rr: null,
      risk_evaluation: {
        status: 'UNKNOWN',
        reasons: ['Open interest unavailable'],
      },
      mode: 'ALERT_ONLY',
      message: 'DEMO: Short B blocked on mock ETHUSD 5m (missing data)',
    },
    {
      ...base,
      signal_id: 'demo-signal-3',
      dedupe_key:
        'demo-tenant|discount-ote-reversal:v1|mock:perp:SOLUSD|LONG|setup-3|PRE_SIGNAL',
      strategy_id: 'discount-ote-reversal',
      strategy_version_id: 'discount-ote-reversal:v1',
      setup_id: 'setup-3',
      instrument_id: 'mock:perp:SOLUSD',
      direction: 'LONG',
      timeframe: '5m',
      kind: 'PRE_SIGNAL',
      setup_state: 'CONFIRMING',
      severity: 'INFO',
      bar_start_ms: close - 7 * barMs,
      bar_close_ms: close - 6 * barMs,
      evaluated_at_ms: close - 6 * barMs + 75,
      inputs_snapshot_hash: hash('e'),
      rule_version: hash('b'),
      reason_tree: reason('/when', 'ALL', 'FALSE', '5 of 6 conditions true', [
        reason(
          '/when/children/3',
          'AOI_TOUCH',
          'TRUE',
          'Touched bullish OB demo-ob-2'
        ),
        reason(
          '/when/children/4',
          'SEQUENCE',
          'FALSE',
          'SSL sweep seen; waiting for seller failure'
        ),
      ]),
      aoi_ids: ['demo-ob-2'],
      range_ids: [],
      ...barVolumes('mock:perp:SOLUSD', close - 7 * barMs, nowMs),
      entry_price: null,
      stop_price: null,
      target_price: null,
      rr: null,
      risk_evaluation: {status: 'NOT_APPLICABLE', reasons: []},
      mode: 'PAPER',
      message: 'DEMO: Long A setup confirming on mock SOLUSD 5m',
    },
  ];
}
