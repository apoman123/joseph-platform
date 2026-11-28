/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Shared private script catalog, the latest study results and
 * attaching owned scripts to charts as studies.
 */

import {
  PythonClient,
  type ChartState,
  type PythonCatalog,
  type ScriptResult,
  type ScriptVersion,
} from '@joseph/contracts';
import {createStore, type WritableStore} from '@joseph/feature-registry';
import type {TerminalRuntime} from '../composition/runtime';
import {ownedStudy} from './study-catalog';

/** Runtime-local authoring state; private sources never enter shared workspaces. */
export interface PythonState {
  /** Fixed-origin authenticated managed-script client. */
  client: PythonClient;
  /** Latest owned catalog or null before loading. */
  catalog: WritableStore<PythonCatalog | null>;
  /** Source selected for editing by the legend or library. */
  editing: WritableStore<ScriptVersion | null>;
  /**
   * Last validated run by `<chart id>:<study id>`; never persisted into
   * exports.
   */
  results: WritableStore<Readonly<Record<string, ScriptResult>>>;
  /**
   * Strategy backtest the Strategy panel shows on charts of its instrument
   * and timeframe, or null; never persisted.
   */
  strategyPreview: WritableStore<ScriptResult | null>;
}

/** Runtime-specific state keeps separate accounts and windows isolated. */
const states = new WeakMap<TerminalRuntime, PythonState>();

/**
 * Returns shared script state without contacting a provider during rendering.
 * @param runtime Current terminal runtime and authenticated market configuration.
 * @return Stable runtime-local catalog, client and authoring stores.
 */
export function pythonState(runtime: TerminalRuntime): PythonState {
  let state = states.get(runtime);
  if (!state) {
    state = {
      client: new PythonClient({
        baseUrl: runtime.config.marketApiUrl ?? '',
        getToken: () => runtime.config.devToken,
      }),
      catalog: createStore<PythonCatalog | null>(null),
      editing: createStore<ScriptVersion | null>(null),
      results: createStore<Readonly<Record<string, ScriptResult>>>({}),
      strategyPreview: createStore<ScriptResult | null>(null),
    };
    states.set(runtime, state);
  }
  return state;
}

/**
 * Attaches an owned visual script's immutable version to a chart as a
 * study, replacing an earlier study of the same script.
 * @param runtime Workspace-owning terminal runtime.
 * @param chart Current chart state.
 * @param script Owned immutable indicator, strategy or alert version.
 */
export function attachPython(
  runtime: TerminalRuntime,
  chart: ChartState,
  script: ScriptVersion
): void {
  if (!['indicator', 'strategy', 'alert'].includes(script.kind)) return;
  runtime.dispatch({
    type: 'addStudy',
    chartId: chart.chart_id,
    study: ownedStudy(script),
  });
}

/**
 * Opens a pinned owned source in the editor and reveals its bottom panel.
 * @param runtime Current authenticated terminal.
 * @param id Owned script identifier.
 * @param version Immutable source version to inspect and edit.
 * @return Resolves after source selection; rejects for inaccessible versions.
 */
export async function editPython(
  runtime: TerminalRuntime,
  id: string,
  version: number
): Promise<void> {
  const state = pythonState(runtime);
  state.editing.set(await state.client.get(id, version));
  await runtime.commands.execute('shell.showPanel', {
    slot: 'bottom-panel',
    id: 'shell.python',
  });
}
