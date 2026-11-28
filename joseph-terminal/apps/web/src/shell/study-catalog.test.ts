/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Resolving migrated plugin layers through the catalog. */

import type {CatalogScript} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {isLegacyPlaceholder, legacyPluginEntry} from './study-catalog';

/**
 * Builds a minimal catalog entry.
 * @param scriptId Catalog id.
 * @param origin Built-in or plugin.
 * @return Catalog script.
 */
function entry(scriptId: string, origin: 'builtin' | 'plugin'): CatalogScript {
  const script: CatalogScript = {
    script_id: scriptId,
    kind: 'indicator',
    origin,
    package: null,
    package_version: null,
    title: scriptId,
    short_title: scriptId,
    description: '',
    category: '',
    overlay: true,
    inputs: {},
    requests: {},
    source_available: origin === 'builtin',
    source_hash: 'h',
    enabled: true,
  };
  return script;
}

describe('legacy plugin layers', () => {
  const catalog = [
    entry('builtin:zones', 'builtin'),
    entry('plugin:pkg:zones', 'plugin'),
    entry('plugin:a:flow', 'plugin'),
    entry('plugin:b:flow', 'plugin'),
  ];

  it('resolves the single plugin script with the old id', () => {
    expect(
      legacyPluginEntry(catalog, {script_id: 'plugin:*:zones'})?.script_id
    ).toBe('plugin:pkg:zones');
  });

  it('stays unresolved when none or several plugins match', () => {
    expect(legacyPluginEntry(catalog, {script_id: 'plugin:*:flow'})).toBe(
      undefined
    );
    expect(legacyPluginEntry(catalog, {script_id: 'plugin:*:none'})).toBe(
      undefined
    );
    expect(legacyPluginEntry(null, {script_id: 'plugin:*:zones'})).toBe(
      undefined
    );
  });

  it('ignores studies that are not placeholders', () => {
    expect(isLegacyPlaceholder({script_id: 'plugin:pkg:zones'})).toBe(false);
    expect(isLegacyPlaceholder({script_id: 'plugin:*:zones'})).toBe(true);
    expect(legacyPluginEntry(catalog, {script_id: 'builtin:zones'})).toBe(
      undefined
    );
  });
});
