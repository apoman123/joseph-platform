/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Conversion of contract candles to render-only chart bars.
 */

import type {ChartBar} from '@joseph/chart-core';
import type {Candle} from '@joseph/contracts';

/**
 * Converts a contract candle to a render bar. Decimal strings become
 * floating-point numbers, which is fine for drawing and never used for
 * order parameters.
 * @param candle Contract candle.
 * @return Bar with display numbers.
 */
export function toChartBar(candle: Candle): ChartBar {
  return {
    timeMs: candle.start_ms,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
    volume: Number(candle.base_volume),
    final: candle.is_final,
  };
}
