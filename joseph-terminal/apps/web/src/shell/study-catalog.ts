/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview The engine's built-in and plugin indicator scripts
 * (`GET /v1/scripts/catalog?kind=indicator`, or the DEMO catalog), loaded
 * once per runtime and shared by the indicator picker, the study legend and
 * the settings dialog. Also turns catalog scripts and owned versions into
 * chart study entries.
 */

'use client';
import type {CatalogScript, ChartState, ScriptVersion} from '@joseph/contracts';
import {createStore, type ReadableStore} from '@joseph/feature-registry';
import {
  LEGACY_PLUGIN_PREFIX,
  nextStudyId,
  type StudyState,
} from '@joseph/terminal-shell';
import type {TerminalRuntime} from '../composition/runtime';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';

/** Catalog requests in flight or answered, per runtime. */
const loads = new WeakMap<TerminalRuntime, Promise<readonly CatalogScript[]>>();

/** Catalog stores, per runtime. */
const stores = new WeakMap<
  TerminalRuntime,
  ReadableStore<readonly CatalogScript[] | null>
>();

/**
 * Loads the indicator catalog once per runtime; a failed request is
 * forgotten, so the next call retries.
 * @param runtime Runtime whose study client is asked.
 * @return Enabled indicator scripts in catalog order.
 */
export function loadStudyCatalog(
  runtime: TerminalRuntime
): Promise<readonly CatalogScript[]> {
  let promise = loads.get(runtime);
  if (!promise) {
    promise = runtime.clients.studies
      .catalog('indicator')
      .then(list => list.scripts.filter(script => script.enabled));
    promise.catch(() => void loads.delete(runtime));
    loads.set(runtime, promise);
  }
  return promise;
}

/**
 * Returns the shared catalog store of a runtime; subscribing starts the
 * request while the list is missing.
 * @param runtime Runtime whose study client is asked.
 * @return Store of the scripts; null until the engine answers.
 */
export function studyCatalog(
  runtime: TerminalRuntime
): ReadableStore<readonly CatalogScript[] | null> {
  const existing = stores.get(runtime);
  if (existing) return existing;
  const inner = createStore<readonly CatalogScript[] | null>(null);
  /** Requests the list unless it has arrived. */
  function request(): void {
    if (inner.get() !== null) return;
    loadStudyCatalog(runtime).then(
      scripts => void inner.set(scripts),
      () => undefined
    );
  }
  const store: ReadableStore<readonly CatalogScript[] | null> = {
    get: () => inner.get(),
    subscribe(listener) {
      request();
      return inner.subscribe(listener);
    },
  };
  stores.set(runtime, store);
  request();
  return store;
}

/**
 * Subscribes a component to the indicator catalog of the current runtime.
 * @return Scripts in catalog order; null until the engine answers.
 */
export function useStudyCatalog(): readonly CatalogScript[] | null {
  return useStore(studyCatalog(useRuntime()));
}

/**
 * Finds the catalog entry a study runs.
 * @param catalog Loaded catalog, or null.
 * @param study Chart study.
 * @return The entry; undefined for owned scripts and unknown ids.
 */
export function catalogEntry(
  catalog: readonly CatalogScript[] | null,
  study: Pick<StudyState, 'script_id'>
): CatalogScript | undefined {
  return catalog?.find(script => script.script_id === study.script_id);
}

/**
 * Finds the installed plugin script that a migrated plugin layer
 * (`plugin:*:<old id>`) stands for: the one plugin entry whose `ID` is the
 * old id. Nothing is named here; the catalog decides.
 * @param catalog Loaded catalog, or null.
 * @param study Chart study.
 * @return That entry; undefined for other studies, before the catalog
 *     loads, or when zero or several plugins match.
 */
export function legacyPluginEntry(
  catalog: readonly CatalogScript[] | null,
  study: Pick<StudyState, 'script_id'>
): CatalogScript | undefined {
  if (!catalog || !isLegacyPlaceholder(study)) return undefined;
  const id = study.script_id.slice(LEGACY_PLUGIN_PREFIX.length);
  const found = catalog.filter(
    script => script.origin === 'plugin' && script.script_id.endsWith(`:${id}`)
  );
  return found.length === 1 ? found[0] : undefined;
}

/**
 * Tells whether a study is a migrated plugin layer awaiting catalog lookup.
 * @param study Chart study.
 * @return True for `plugin:*:<old id>` script ids.
 */
export function isLegacyPlaceholder(
  study: Pick<StudyState, 'script_id'>
): boolean {
  return study.script_id.startsWith(LEGACY_PLUGIN_PREFIX);
}

/**
 * Returns the legend name of a study.
 * @param catalog Loaded catalog, or null.
 * @param study Chart study.
 * @return The catalog's short title, else the stored name.
 */
export function studyName(
  catalog: readonly CatalogScript[] | null,
  study: StudyState
): string {
  return catalogEntry(catalog, study)?.short_title || study.name;
}

/**
 * Builds a new study entry for a catalog script.
 * @param chart Chart receiving it.
 * @param script Catalog script.
 * @return Visible study with default inputs and a unique study id.
 */
export function catalogStudy(
  chart: ChartState,
  script: CatalogScript
): StudyState {
  return {
    study_id: nextStudyId(chart, script.script_id),
    script_id: script.script_id,
    version: 0,
    kind: 'indicator',
    name: (script.short_title || script.title).slice(0, 120),
    visible: true,
    inputs: {},
    colors: {},
  };
}

/**
 * Builds the study entry of an owned script version; it replaces an
 * existing study of the same script.
 * @param script Owned indicator, strategy or alert version.
 * @return Visible study pinned to the version with its default inputs.
 */
export function ownedStudy(script: ScriptVersion): StudyState {
  return {
    study_id: script.script_id,
    script_id: script.script_id,
    version: script.version,
    kind:
      script.kind === 'strategy' || script.kind === 'alert'
        ? script.kind
        : 'indicator',
    name: script.name.slice(0, 120),
    visible: true,
    inputs: {...script.defaults},
    colors: {},
  };
}
