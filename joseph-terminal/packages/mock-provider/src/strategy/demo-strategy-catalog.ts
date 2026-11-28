/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview DEMO descriptions of the market engine's built-in Strategy
 * scripts and a synthetic, clearly labelled DEMO backtest. DEMO mode has no
 * Python runtime: these are display data, not strategy implementations; the
 * real strategies run only in the market engine.
 */

import {
  TIMEFRAME_MS,
  type CatalogScript,
  type PlotPoint,
  type ScriptResult,
  type ScriptRun,
  type SimulatedFill,
  type StrategyOrder,
  type StrategyScript,
  type StrategyTrade,
} from '@joseph/contracts';
import {demoCalendar} from '../market/demo-sessions';
import {DEMO_INSTRUMENT_SPECS, demoCandle} from '../market/prices';

/** One declared input in the catalog shape. */
type DemoInput = NonNullable<CatalogScript['inputs'][string]>;

/**
 * Builds a numeric input declaration.
 * @param value Default.
 * @param title Label.
 * @param minimum Inclusive lower bound.
 * @param maximum Inclusive upper bound.
 * @param group Settings section (default none).
 * @return Input declaration.
 */
function num(
  value: number,
  title: string,
  minimum: number,
  maximum: number,
  group = ''
): DemoInput {
  return {
    default: value,
    title,
    minimum,
    maximum,
    options: null,
    step: null,
    group,
    tooltip: '',
  };
}

/**
 * Builds a boolean, text or choice input declaration.
 * @param value Default.
 * @param title Label.
 * @param options Accepted values, or null for free input.
 * @return Input declaration.
 */
function other(
  value: boolean | string,
  title: string,
  options: string[] | null = null
): DemoInput {
  return {
    default: value,
    title,
    minimum: null,
    maximum: null,
    options,
    step: null,
    group: '',
    tooltip: '',
  };
}

/**
 * Builds the risk inputs shared by the DEMO descriptions.
 * @param stop Default stop distance or buffer in ATRs.
 * @param target Default target R multiple, or null without one.
 * @param minRr Default minimum net reward/risk.
 * @return Input declarations by name.
 */
function risk(
  stop: number,
  target: number | null,
  minRr: number
): Record<string, DemoInput> {
  return {
    atr_length: num(14, 'ATR length', 1, 100, 'Risk'),
    stop_atr: num(stop, 'Stop distance (ATR)', 0, 20, 'Risk'),
    ...(target === null
      ? {}
      : {target_r: num(target, 'Target (R multiple)', 0.5, 20, 'Risk')}),
    min_rr: num(minRr, 'Minimum net reward/risk', 0, 20, 'Risk'),
  };
}

/** The built-in strategies, as the market engine's catalog lists them. */
export const DEMO_BUILTIN_STRATEGIES: readonly StrategyScript[] = [
  {
    script_id: 'builtin:ema-trend-long',
    origin: 'builtin',
    title: 'EMA Trend Long',
    description:
      'Long when the close crosses above the EMA while the one-hour RSI is above 50 and volume delta is positive; ATR stop, fixed 3R target.',
    version: 0,
    inputs: {
      ema_length: num(20, 'EMA length', 1, 500),
      rsi_length: num(14, '1h RSI length', 2, 100),
      rsi_threshold: num(50, '1h RSI above', 0, 100),
      require_flow: other(true, 'Require positive volume delta'),
      ...risk(1.5, 3, 2),
      cooldown_bars: num(12, 'Cooldown (bars)', 0, 500),
    },
    source_available: true,
    enabled: true,
  },
  {
    script_id: 'builtin:rsi-reversion-short',
    origin: 'builtin',
    title: 'RSI Reversion Short',
    description:
      'Short when the RSI falls back below 70 within three bars of a close above the upper Bollinger Band; swing-high stop, fixed 2R target.',
    version: 0,
    inputs: {
      rsi_length: num(14, 'RSI length', 2, 100),
      rsi_level: num(70, 'RSI crosses below', 1, 99),
      bb_length: num(20, 'Bollinger length', 2, 500),
      bb_mult: num(2, 'Bollinger multiplier', 0.1, 10),
      within_bars: num(3, 'Band close within (bars)', 1, 50),
      ...risk(0.5, 2, 1.5),
      cooldown_bars: num(12, 'Cooldown (bars)', 0, 500),
    },
    source_available: true,
    enabled: true,
  },
  {
    script_id: 'builtin:pivot-bounce-long',
    origin: 'builtin',
    title: 'Pivot Bounce Long',
    description:
      'Long on a wick touch of the daily S1 or S2 pivot between 09:30 and 11:30 New York time on weekdays; stop below the level, target the nearest of P and R1.',
    version: 0,
    inputs: {
      supports: other('S1 and S2', 'Support levels', ['S1', 'S2', 'S1 and S2']),
      targets: other('P and R1', 'Target levels', ['P', 'R1', 'P and R1']),
      session_start: other('09:30', 'Window start (New York)'),
      session_end: other('11:30', 'Window end (New York)'),
      ...risk(0.2, null, 1.5),
      cooldown_bars: num(6, 'Cooldown (bars)', 0, 500),
    },
    source_available: true,
    enabled: true,
  },
];

/** Built-ins that sell short. */
const SHORT_STRATEGIES = new Set(['builtin:rsi-reversion-short']);

/** Bars between DEMO entries, and bars held. */
const SPACING = 40;
const HOLD = 12;

/**
 * Builds a clearly labelled synthetic DEMO backtest on DEMO candles: one
 * trade every 40 bars, held 12 bars, entered and exited at next-bar opens.
 * @param scriptId Strategy id.
 * @param request Instrument, timeframe, bar count and initial equity.
 * @param nowMs Current time in epoch ms.
 * @return Result with fills, orders, trades, equity and metrics, labelled
 *     `DEMO`; empty for unknown instruments.
 */
export function demoBacktest(
  scriptId: string,
  request: ScriptRun,
  nowMs: number
): ScriptResult {
  const spec = DEMO_INSTRUMENT_SPECS.find(
    s => s.instrumentId === request.instrument_id
  );
  const tf = request.tf;
  const count = Math.min(request.limit ?? 500, 2000);
  const calendar = demoCalendar(spec?.calendar ?? '24x7');
  const barMs = TIMEFRAME_MS[tf];
  const current = calendar.continuous
    ? Math.floor(nowMs / barMs) * barMs
    : calendar.floor(tf, nowMs);
  const starts: number[] = [];
  for (let i = count; i >= 1 && spec; i--) {
    starts.push(
      calendar.continuous
        ? current - i * barMs
        : calendar.shift(tf, current, -i)
    );
  }
  const bars = spec
    ? starts.map(start => demoCandle(spec, tf, start, nowMs, calendar))
    : [];
  const sign = SHORT_STRATEGIES.has(scriptId) ? -1 : 1;
  const initial = request.initial_equity ?? 10_000;
  const fills: SimulatedFill[] = [];
  const orders: StrategyOrder[] = [];
  const trades: StrategyTrade[] = [];
  const equity: PlotPoint[] = [];
  let cash = initial;
  let position = 0;
  let entryPrice = 0;
  let entryMs = 0;
  bars.forEach((bar, i) => {
    const open = Number(bar.open);
    const close = Number(bar.close);
    const queued = orders.at(-1);
    if (queued && queued.status === 'pending') {
      const qty = queued.action === 'close' ? position : queued.quantity * sign;
      const change = queued.action === 'close' ? -position : qty;
      fills.push({
        time_ms: bar.start_ms,
        side: change > 0 ? 'buy' : 'sell',
        quantity: Math.abs(change),
        price: open,
        fee: 0,
        effect: queued.action === 'close' ? 'exit' : 'entry',
      });
      queued.status = 'filled';
      queued.fill_time_ms = bar.start_ms;
      cash -= change * open;
      if (queued.action === 'close') {
        const pnl = position * (open - entryPrice);
        trades.push({
          direction: sign > 0 ? 'LONG' : 'SHORT',
          entry_time_ms: entryMs,
          exit_time_ms: bar.start_ms,
          quantity: Math.abs(position),
          entry_price: entryPrice,
          exit_price: open,
          fees: 0,
          pnl,
          return_pct: (pnl / (entryPrice * Math.abs(position))) * 100,
        });
        position = 0;
      } else {
        position = qty;
        entryPrice = open;
        entryMs = bar.start_ms;
      }
    }
    if (i < bars.length - 1 && i % SPACING === SPACING - HOLD - 1) {
      const quantity = (0.9 * (cash + position * close)) / close;
      orders.push({
        time_ms: bar.start_ms,
        action: sign > 0 ? 'long' : 'short',
        quantity,
        reference: close,
        stop: close * (1 - sign * 0.01),
        target: close * (1 + sign * 0.02),
        comment: 'DEMO entry',
        status: 'pending',
        fill_time_ms: null,
      });
    } else if (position && i % SPACING === SPACING - 1) {
      orders.push({
        time_ms: bar.start_ms,
        action: 'close',
        quantity: 0,
        reference: close,
        stop: null,
        target: null,
        comment: 'DEMO exit',
        status: 'pending',
        fill_time_ms: null,
      });
    }
    equity.push({time_ms: bar.start_ms, value: cash + position * close});
  });
  const final = equity.at(-1)?.value ?? initial;
  let peak = initial;
  let drawdown = 0;
  for (const point of equity) {
    const value = point.value ?? initial;
    peak = Math.max(peak, value);
    drawdown = Math.max(drawdown, ((peak - value) / peak) * 100);
  }
  const wins = trades.filter(t => (t.pnl ?? 0) > 0).length;
  return {
    script_id: scriptId,
    version: 0,
    kind: 'strategy',
    source_hash: 'demo',
    instrument_id: request.instrument_id,
    tf,
    bars_count: bars.length,
    feed_state: 'DEMO',
    data_source: 'DEMO',
    complete: true,
    overlay: true,
    plots: [],
    fills,
    orders,
    trades,
    position_size: position,
    equity_curve: equity,
    metrics: {
      initial_equity: initial,
      final_equity: final ?? initial,
      net_profit: (final ?? initial) - initial,
      return_pct: ((final ?? initial) / initial - 1) * 100,
      max_drawdown_pct: drawdown,
      fill_count: fills.length,
      fees: 0,
      trade_count: trades.length,
      win_rate_pct: trades.length ? (wins / trades.length) * 100 : 0,
      unfilled_intents: 0,
      rejected_orders: 0,
    },
  };
}
