/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Test helper building a vocabulary from the shared plugin
 * descriptor fixtures, plus a Bollinger Bands descriptor the templates use.
 */

import type {IndicatorPluginInfo, ValueSpec} from '@joseph/contracts';
import {buildVocabulary, type Vocabulary} from '../vocabulary';
import {readFixture} from './schema';

/**
 * Reads the valid plugin descriptors of the shared fixtures.
 * @return Descriptors in fixture order.
 */
export function fixturePluginInfos(): IndicatorPluginInfo[] {
  // The fixture's valid examples are checked against `IndicatorPluginInfo`
  // by the contracts tests.
  return readFixture('indicator-plugin').valid as IndicatorPluginInfo[];
}

/**
 * Creates a plugin descriptor for tests.
 * @param base Descriptor to copy.
 * @param patch Fields to replace.
 * @return New descriptor.
 */
export function pluginInfo(
  base: IndicatorPluginInfo,
  patch: Partial<IndicatorPluginInfo>
): IndicatorPluginInfo {
  return {...base, ...patch};
}

/**
 * Builds the test vocabulary: the fixture plugins plus Bollinger Bands.
 * @return Vocabulary in fixture order, Bollinger Bands last.
 * @throws `Error` when the fixtures have no plugin descriptor.
 */
export function testVocabulary(): Vocabulary {
  const infos = fixturePluginInfos();
  const [first] = infos;
  if (!first) throw new Error('missing plugin fixtures');
  const band = (key: string): ValueSpec => ({
    key,
    type: 'decimal',
    unit: 'price',
    description: `Band ${key}.`,
  });
  return buildVocabulary([
    ...infos,
    pluginInfo(first, {
      plugin_id: 'bollinger_bands',
      display_name: 'Bollinger Bands',
      output_kinds: [],
      output_events: [],
      output_values: [band('basis'), band('upper'), band('lower')],
    }),
  ]);
}
