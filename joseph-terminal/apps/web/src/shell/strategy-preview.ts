/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Paints the Strategy panel's backtest (plots and entry/exit
 * markers) on charts showing the backtested instrument and timeframe.
 */

'use client';
import type {ChartPort} from '@joseph/chart-core';
import type {ChartState} from '@joseph/contracts';
import {useEffect} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {pythonCanvas} from './python-painter';
import {pythonState} from './python-state';
import {useStore} from './react-bindings';

/**
 * Adds the shared strategy backtest as a canvas layer while it matches the
 * chart's instrument and timeframe.
 * @param port Mounted chart renderer, or null before mount.
 * @param chart Chart identity, symbol and timeframe.
 * @param runtime Terminal runtime holding the shared preview.
 */
export function useStrategyPreview(
  port: ChartPort | null,
  chart: ChartState,
  runtime: TerminalRuntime
): void {
  const preview = useStore(pythonState(runtime).strategyPreview);
  useEffect(() => {
    if (
      !port ||
      !preview ||
      preview.instrument_id !== chart.instrument_id ||
      preview.tf !== chart.timeframe
    ) {
      return;
    }
    const remove = port.addCanvasLayer(
      pythonCanvas(`strategy-preview:${chart.chart_id}`, {
        ...preview,
        overlay: true,
      })
    );
    port.invalidate();
    return () => {
      remove();
      port.invalidate();
    };
  }, [port, preview, chart.chart_id, chart.instrument_id, chart.timeframe]);
}
