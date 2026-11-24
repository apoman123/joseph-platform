/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview The vocabulary is built from plugin descriptors only.
 */

import {describe, expect, it} from 'vitest';
import {fixturePluginInfos, pluginInfo} from './testing/vocabulary';
import {
  EMPTY_VOCABULARY,
  buildVocabulary,
  eventLabel,
  kindLabel,
  pluginName,
  stateValues,
} from './vocabulary';

/**
 * Finds a fixture descriptor by id.
 * @param id Plugin id.
 * @return Descriptor.
 * @throws `Error` when the fixtures lack it.
 */
function fixture(id: string) {
  const info = fixturePluginInfos().find(p => p.plugin_id === id);
  if (!info) throw new Error(`missing fixture ${id}`);
  return info;
}

describe('buildVocabulary', () => {
  it('maps kinds and events to their plugins with display labels', () => {
    const vocab = buildVocabulary(fixturePluginInfos());
    expect([...vocab.plugins.keys()]).toEqual([
      'ema',
      'rsi',
      'volume_delta',
      'pivot_points',
      'example_zones',
    ]);
    expect(vocab.kinds.get('PIVOT_S1')).toEqual({
      pluginId: 'pivot_points',
      label: 'S1',
    });
    expect(vocab.kinds.get('EXAMPLE_ZONE')?.pluginId).toBe('example_zones');
    expect(vocab.events.get('EXAMPLE_BREAK_UP')).toEqual({
      pluginIds: ['example_zones'],
      label: 'Break up',
    });
    expect([...vocab.events.keys()].slice(-2)).toEqual([
      'AOI_INVALIDATED',
      'AOI_EXPIRED',
    ]);
    expect(vocab.events.get('AOI_EXPIRED')?.pluginIds).toEqual([
      'pivot_points',
      'example_zones',
    ]);
  });

  it('keeps every publisher of a shared event in registration order', () => {
    const zones = fixture('example_zones');
    const copy = pluginInfo(zones, {
      plugin_id: 'other_zones',
      display_name: 'Other zones',
      output_kinds: ['OTHER_ZONE', 'EXAMPLE_ZONE'],
      display: {...zones.display, kinds: [], events: []},
    });
    const vocab = buildVocabulary([copy, zones]);
    expect(vocab.events.get('EXAMPLE_BREAK_UP')).toEqual({
      pluginIds: ['other_zones', 'example_zones'],
      label: 'Break up',
    });
    expect(vocab.kinds.get('EXAMPLE_ZONE')).toEqual({
      pluginId: 'other_zones',
      label: 'EXAMPLE_ZONE',
    });
  });

  it('falls back to raw names without plugins', () => {
    expect(EMPTY_VOCABULARY.events.size).toBe(0);
    expect(kindLabel(EMPTY_VOCABULARY, 'ZONE_HIGH')).toBe('ZONE_HIGH');
    expect(eventLabel(EMPTY_VOCABULARY, 'SIGNAL_UP')).toBe('SIGNAL_UP');
    expect(eventLabel(EMPTY_VOCABULARY, 'AOI_EXPIRED')).toBe('AOI expired');
    expect(pluginName(EMPTY_VOCABULARY, 'ema')).toBe('ema');
    expect(buildVocabulary([fixture('ema')]).events.size).toBe(0);
  });

  it('lists the values a state condition can match', () => {
    expect(stateValues(fixture('volume_delta')).map(v => v.key)).toEqual([
      'method',
    ]);
    expect(stateValues(fixture('example_zones')).map(v => v.key)).toEqual([
      'last_break',
      'armed',
    ]);
    expect(stateValues(fixture('ema'))).toEqual([]);
  });
});
