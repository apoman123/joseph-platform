/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Workspace schema v1 → v2 migration: the per-chart
 * indicator layers of the retired indicator plugin system and the owned
 * Python scripts attached under `panels['python.<chart id>']` become one
 * list of study entries (`ChartState.studies`).
 *
 * - A built-in layer becomes a `builtin:<id>` study when it was shown or
 *   explicitly added (`present`); hidden default layers and removed ones
 *   are dropped. Its settings become script inputs (`tick_size` is gone;
 *   `delta_source: 'estimate'` turns `use_buy_volume` off) and its value
 *   colors become plot colors.
 * - A shown or added layer of another plugin package becomes a placeholder
 *   study `plugin:*:<old id>` (see `LEGACY_PLUGIN_PREFIX`). Once the
 *   catalog loads, the terminal resolves it to the one installed plugin
 *   script whose `ID` equals the old id; until then, or when no or several
 *   plugins match, it stays in the legend as not installed. Its settings
 *   become inputs by the same rules (decimal strings become numbers);
 *   value colors are dropped because plot names are the plugin's own.
 * - Attached owned scripts keep their pinned version, inputs and colors.
 */

import type {StudyState} from './workspace';

/** Script-id prefix of a migrated plugin layer awaiting catalog lookup. */
export const LEGACY_PLUGIN_PREFIX = 'plugin:*:';

/** Plugin ids of schema v1 (`plugin_id` of an indicator layer). */
const V1_PLUGIN_ID = /^[a-z][a-z0-9_]{0,63}$/;

/** A decimal string of a v1 numeric setting. */
const DECIMAL_TEXT = /^-?\d+(?:\.\d+)?$/;

/** Built-in plugin ids of schema v1 and their legend names. */
const BUILTIN_NAMES: Readonly<Record<string, string>> = {
  sma: 'SMA',
  ema: 'EMA',
  anchored_vwap: 'VWAP',
  bollinger_bands: 'BB',
  rsi: 'RSI',
  atr: 'ATR',
  volume_delta: 'Delta %',
  pivot_points: 'Pivots',
};

/** Plot names of the v1 value keys that carried colors. */
const PLOT_NAMES: Readonly<Record<string, string>> = {
  sma: 'SMA',
  ema: 'EMA',
  rsi: 'RSI',
  atr: 'ATR',
  basis: 'Basis',
  upper: 'Upper',
  lower: 'Lower',
  vwap: 'VWAP',
  delta_pct: 'Delta %',
};

/** Settings whose v1 values were decimal strings. */
const DECIMAL_SETTINGS = new Set([
  'mult',
  'band_1',
  'band_2',
  'band_3',
  'deviations',
  'min_coverage',
]);

/** Owned script slugs (contract `ScriptId`). */
const OWNED_SCRIPT = /^[a-z][a-z0-9-]{0,62}$/;

/** Hex colors accepted as plot colors. */
const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/**
 * Reads a plain object, or nothing.
 * @param value Untrusted value.
 * @return The object, or null for arrays and non-objects.
 */
function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/**
 * Converts v1 plugin settings to built-in script inputs.
 * @param pluginId v1 plugin id.
 * @param settings v1 settings.
 * @return Input overrides of the built-in script.
 */
export function inputsFromLayerSettings(
  pluginId: string,
  settings: unknown
): Record<string, unknown> {
  const inputs: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record(settings) ?? {})) {
    if (key === 'tick_size') continue;
    if (key === 'delta_source') {
      inputs.use_buy_volume = value !== 'estimate';
      continue;
    }
    const builtin = pluginId in BUILTIN_NAMES;
    if (
      typeof value === 'string' &&
      (builtin ? DECIMAL_SETTINGS.has(key) : DECIMAL_TEXT.test(value))
    ) {
      const number = Number(value);
      if (Number.isFinite(number)) inputs[key] = number;
      continue;
    }
    inputs[key] = value;
  }
  return inputs;
}

/**
 * Converts v1 value colors to plot colors.
 * @param style v1 layer style.
 * @return Plot name to hex color.
 */
function colorsFromStyle(style: unknown): Record<string, string> {
  const colors: Record<string, string> = {};
  for (const [key, value] of Object.entries(
    record(record(style)?.colors) ?? {}
  )) {
    const plot = PLOT_NAMES[key];
    if (plot && typeof value === 'string' && HEX_COLOR.test(value)) {
      colors[plot] = value;
    }
  }
  return colors;
}

/**
 * Converts the v1 indicator layers of one chart to studies.
 * @param layers v1 `indicator_layers`.
 * @return Studies of the layers that were shown or added: built-ins, and
 *     placeholders for other plugin packages.
 */
export function studiesFromLayers(layers: unknown): StudyState[] {
  if (!Array.isArray(layers)) return [];
  const studies: StudyState[] = [];
  for (const raw of layers) {
    const layer = record(raw);
    const pluginId = layer?.plugin_id;
    if (typeof pluginId !== 'string' || !V1_PLUGIN_ID.test(pluginId)) {
      continue;
    }
    if (layer?.removed === true) continue;
    const visible = layer?.visible === true;
    if (!visible && layer?.present !== true) continue;
    const builtin = pluginId in BUILTIN_NAMES;
    const scriptId = builtin
      ? `builtin:${pluginId}`
      : `${LEGACY_PLUGIN_PREFIX}${pluginId}`;
    if (studies.some(study => study.study_id === scriptId)) continue;
    studies.push({
      study_id: scriptId,
      script_id: scriptId,
      version: 0,
      kind: 'indicator',
      name: BUILTIN_NAMES[pluginId] ?? pluginId,
      visible,
      inputs: inputsFromLayerSettings(pluginId, layer?.settings),
      colors: builtin ? colorsFromStyle(layer?.style) : {},
    });
  }
  return studies;
}

/**
 * Converts the owned scripts attached to one chart to studies.
 * @param attachments v1 `panels['python.<chart id>']`.
 * @return Studies of the well-formed attachments.
 */
export function studiesFromAttachments(attachments: unknown): StudyState[] {
  if (!Array.isArray(attachments)) return [];
  const studies: StudyState[] = [];
  for (const raw of attachments) {
    const row = record(raw);
    if (
      !row ||
      typeof row.script_id !== 'string' ||
      !OWNED_SCRIPT.test(row.script_id) ||
      typeof row.version !== 'number' ||
      !Number.isSafeInteger(row.version) ||
      row.version < 1 ||
      typeof row.name !== 'string' ||
      !row.name
    ) {
      continue;
    }
    const kind =
      row.kind === 'strategy' || row.kind === 'alert' ? row.kind : 'indicator';
    const colors: Record<string, string> = {};
    for (const [key, value] of Object.entries(record(row.colors) ?? {})) {
      if (typeof value === 'string' && HEX_COLOR.test(value)) {
        colors[key] = value;
      }
    }
    studies.push({
      study_id: row.script_id,
      script_id: row.script_id,
      version: row.version,
      kind,
      name: row.name.slice(0, 120),
      visible: row.visible !== false,
      inputs: {...(record(row.inputs) ?? {})},
      colors,
    });
  }
  return studies;
}

/**
 * Migrates a schema v1 workspace candidate to schema v2. Shapes it does
 * not recognize are passed through for `validateWorkspace` to reject.
 * @param raw Parsed v1 document.
 * @return v2 candidate, not yet validated.
 */
export function migrateWorkspaceV1(raw: unknown): unknown {
  const doc = record(raw);
  if (!doc || !Array.isArray(doc.charts)) return raw;
  const panels = {...(record(doc.panels) ?? {})};
  const charts = doc.charts.map(value => {
    const chart = record(value);
    if (!chart) return value;
    const {indicator_layers: layers, ...rest} = chart;
    const key = `python.${String(chart.chart_id)}`;
    const attached = studiesFromAttachments(panels[key]);
    delete panels[key];
    const studies = [...studiesFromLayers(layers)];
    for (const study of attached) {
      if (!studies.some(item => item.study_id === study.study_id)) {
        studies.push(study);
      }
    }
    return {...rest, studies: studies.slice(0, 40)};
  });
  return {...doc, schema_version: 2, charts, panels};
}
