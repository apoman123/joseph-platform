/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO signal fixtures shaped exactly like backend output:
 * strategy activations emit `ENTRY_CONFIRMED` and `POSITION_EXITED`. They
 * are static examples for offline UI work, not computed signals.
 */

import type {Signal} from '@joseph/contracts';

/**
 * Creates DEMO signals relative to a reference time, one per built-in
 * strategy.
 * @param nowMs Reference time in epoch ms (UTC); the first two signals sit
 *     on the 5m bars that closed 0 and 3 bars before the 5-minute boundary
 *     at or before it, the third on the 15m bar that closed 2 bars before
 *     the 15-minute boundary.
 * @return Three signals (paper entry, exit, alert-only entry), newest
 *     first.
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
    data_source: 'DEMO' as const,
    condition: null,
  };
  return [
    {
      ...base,
      signal_id: 'demo-signal-1',
      dedupe_key: `strategy|demo-tenant|demo-act-1|${hash('a')}|mock:perp:BTCUSD|${close - barMs}|ENTRY_CONFIRMED|LONG`,
      strategy_id: 'builtin:pivot-bounce-long',
      strategy_version_id: 'builtin:pivot-bounce-long:vdemo',
      source_hash: hash('a'),
      instrument_id: 'mock:perp:BTCUSD',
      direction: 'LONG',
      timeframe: '5m',
      kind: 'ENTRY_CONFIRMED',
      severity: 'ENTRY',
      mode: 'PAPER',
      bar_start_ms: close - barMs,
      bar_close_ms: close,
      evaluated_at_ms: close + 120,
      entry_price: '94790',
      stop_price: '94650',
      target_price: '95100',
      rr: '2.21',
      message:
        'DEMO: Pivot bounce (long) LONG entry on mock:perp:BTCUSD 5m: bounce off pivot S1',
    },
    {
      ...base,
      signal_id: 'demo-signal-2',
      dedupe_key: `strategy|demo-tenant|demo-act-2|${hash('b')}|mock:perp:ETHUSD|${close - 4 * barMs}|POSITION_EXITED|SHORT`,
      strategy_id: 'builtin:rsi-reversion-short',
      strategy_version_id: 'builtin:rsi-reversion-short:vdemo',
      source_hash: hash('b'),
      instrument_id: 'mock:perp:ETHUSD',
      direction: 'SHORT',
      timeframe: '5m',
      kind: 'POSITION_EXITED',
      severity: 'INFO',
      mode: 'ALERT_ONLY',
      bar_start_ms: close - 4 * barMs,
      bar_close_ms: close - 3 * barMs,
      evaluated_at_ms: close - 3 * barMs + 90,
      entry_price: null,
      stop_price: null,
      target_price: null,
      rr: null,
      message:
        'DEMO: RSI reversion (short) SHORT exit on mock:perp:ETHUSD 5m: RSI back below 50',
    },
    {
      ...base,
      signal_id: 'demo-signal-3',
      dedupe_key: `strategy|demo-tenant|demo-act-3|${hash('c')}|mock:perp:SOLUSD|${slowClose - 3 * slowBarMs}|ENTRY_CONFIRMED|LONG`,
      strategy_id: 'builtin:ema-trend-long',
      strategy_version_id: 'builtin:ema-trend-long:vdemo',
      source_hash: hash('c'),
      instrument_id: 'mock:perp:SOLUSD',
      direction: 'LONG',
      timeframe: '15m',
      kind: 'ENTRY_CONFIRMED',
      severity: 'ENTRY',
      mode: 'ALERT_ONLY',
      bar_start_ms: slowClose - 3 * slowBarMs,
      bar_close_ms: slowClose - 2 * slowBarMs,
      evaluated_at_ms: slowClose - 2 * slowBarMs + 75,
      entry_price: '182.4',
      stop_price: '179.9',
      target_price: null,
      rr: null,
      message:
        'DEMO: EMA trend (long) LONG entry on mock:perp:SOLUSD 15m: close crossed above the EMA',
    },
  ];
}
