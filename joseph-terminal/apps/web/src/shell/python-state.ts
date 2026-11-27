/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Shared private script catalog and saved chart attachments. */

import {
  PythonClient,
  type ChartState,
  type PythonCatalog,
  type ScriptResult,
  type ScriptVersion,
} from '@joseph/contracts';
import {createStore, type WritableStore} from '@joseph/feature-registry';
import type {TerminalRuntime} from '../composition/runtime';

/** A pinned managed study attached to one saved chart. */
export interface PythonAttachment {
  /** Tenant-owned immutable source identifier. */
  script_id: string;
  /** Optional role retained for provisional indicator refreshes. */
  kind?: ScriptVersion['kind'];
  /** Positive immutable version, retained when the source head changes. */
  version: number;
  /** User-facing title from that version. */
  name: string;
  /** Visibility; false preserves the source pin and all settings. */
  visible: boolean;
  /** Per-chart JSON input overrides. */
  inputs: Record<string, unknown>;
  /** Per-series hex style overrides. */
  colors: Record<string, string>;
}

/** Runtime-local authoring state; private sources never enter shared workspaces. */
export interface PythonState {
  /** Fixed-origin authenticated managed-script client. */
  client: PythonClient;
  /** Latest owned catalog or null before loading. */
  catalog: WritableStore<PythonCatalog | null>;
  /** Source selected for editing by the legend or library. */
  editing: WritableStore<ScriptVersion | null>;
  /** Last validated run by chart/attachment; never persisted into exports. */
  results: WritableStore<Readonly<Record<string, ScriptResult>>>;
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
    };
    states.set(runtime, state);
  }
  return state;
}

/**
 * Reads validated pinned attachments from untrusted workspace panel data.
 * @param runtime Workspace-owning runtime.
 * @param chartId Exact chart tab identity.
 * @return Valid persisted attachments; malformed imported entries are ignored.
 */
export function pythonAttachments(
  runtime: TerminalRuntime,
  chartId: string
): PythonAttachment[] {
  const raw = (
    runtime.workspace.get().panels as Record<string, unknown> | undefined
  )?.[`python.${chartId}`];
  if (!Array.isArray(raw)) return [];
  return raw.filter((value): value is PythonAttachment => {
    if (!value || typeof value !== 'object') return false;
    const row = value as Partial<PythonAttachment>;
    return (
      typeof row.script_id === 'string' &&
      /^[a-z][a-z0-9-]{0,62}$/.test(row.script_id) &&
      typeof row.version === 'number' &&
      Number.isSafeInteger(row.version) &&
      row.version > 0 &&
      typeof row.name === 'string' &&
      typeof row.visible === 'boolean' &&
      !!row.inputs &&
      typeof row.inputs === 'object' &&
      !!row.colors &&
      typeof row.colors === 'object'
    );
  });
}

/**
 * Saves one chart's attachment collection through the normal workspace reducer.
 * @param runtime Workspace-owning terminal runtime.
 * @param chartId Target tab identity.
 * @param rows Pinned study configurations; source code is never persisted here.
 */
export function savePythonAttachments(
  runtime: TerminalRuntime,
  chartId: string,
  rows: PythonAttachment[]
): void {
  runtime.dispatch({
    type: 'setPanel',
    featureId: `python.${chartId}`,
    state: rows,
  });
}

/**
 * Attaches or replaces an owned visual script's immutable version on a chart.
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
  const rows = pythonAttachments(runtime, chart.chart_id);
  savePythonAttachments(runtime, chart.chart_id, [
    ...rows.filter(row => row.script_id !== script.script_id),
    {
      script_id: script.script_id,
      kind: script.kind,
      version: script.version,
      name: script.name,
      visible: true,
      inputs: {...script.defaults},
      colors: {},
    },
  ]);
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
