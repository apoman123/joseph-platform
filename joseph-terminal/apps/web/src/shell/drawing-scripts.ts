/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Runs the built-in script behind an engine-backed drawing
 * (anchored VWAP, regression channel) through the chart's study client:
 * closed bars from the drawing's first bar, at most the engine's run limit.
 */

import {ApiError, type ScriptResult} from '@joseph/contracts';
import {
  ENGINE_ANCHOR_BAR_CAP,
  type DrawingSeriesQuery,
} from '@joseph/drawing-core';
import type {TerminalRuntime} from '../composition/runtime';

/** Shown when a drawing's anchor is beyond the bars a run reads. */
export const ANCHOR_OUTSIDE_MESSAGE = `the anchor is before the bars the engine reads (at most ${ENGINE_ANCHOR_BAR_CAP.toLocaleString('en-US')} bars back); move it closer or use a higher timeframe`;

/**
 * Runs a drawing's script.
 * @param runtime Runtime whose study client runs it.
 * @param query Script, first bar and inputs of the drawing.
 * @return The script result.
 * @throws `Error` with the engine's message when the run fails, and an
 *     `ApiError` refusal when the script reports its anchor outside the
 *     loaded bars.
 */
export async function runDrawingScript(
  runtime: TerminalRuntime,
  query: DrawingSeriesQuery
): Promise<ScriptResult> {
  const answer = await runtime.clients.studies.runBatch({
    instrument_id: query.instrumentId,
    tf: query.timeframe,
    from_ms: query.fromMs,
    limit: ENGINE_ANCHOR_BAR_CAP,
    include_open: false,
    runs: [{script_id: query.scriptId, inputs: {...query.inputs}}],
  });
  const entry = answer.results[0];
  if (!entry?.result) throw new Error(entry?.error ?? 'Script run failed');
  const outside = (entry.result.visuals ?? []).some(
    visual =>
      visual.kind === 'table' &&
      JSON.stringify(visual.data.rows ?? []).includes('outside the loaded bars')
  );
  if (outside) {
    throw new ApiError(422, {
      code: 'VALIDATION_FAILED',
      message: ANCHOR_OUTSIDE_MESSAGE,
      trace_id: '',
      retryable: false,
      field_errors: [],
    });
  }
  return entry.result;
}
