/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Rule vocabulary built from the indicator plugins the engine
 * publishes: which plugin owns each area-of-interest (AOI) kind, which
 * plugins publish each event, the values each plugin publishes and the
 * labels the plugins give them. The editor builds menus, sentences and
 * membership checks from it; nothing here names a particular plugin.
 */

import type {
  AoiKind,
  IndicatorPluginInfo,
  StructureEventName,
  ValueSpec,
} from '@joseph/contracts';

/** An AOI kind and the plugin that publishes it. */
export interface VocabularyKind {
  /** Plugin publishing the kind (kinds are unique across plugins). */
  pluginId: string;
  /** Label from the plugin's display hints, or the raw kind. */
  label: string;
}

/** An event name and the plugins that publish it. */
export interface VocabularyEvent {
  /**
   * Plugins publishing the event, in registration order. For a reserved
   * event these are the plugins that publish AOIs.
   */
  pluginIds: readonly string[];
  /** Label from the first publisher that styles it, or the raw name. */
  label: string;
}

/** Everything the editor knows about the installed plugins. */
export interface Vocabulary {
  /** Plugin descriptors by id, in registration order. */
  plugins: ReadonlyMap<string, IndicatorPluginInfo>;
  /** AOI kinds in registration order. */
  kinds: ReadonlyMap<AoiKind, VocabularyKind>;
  /** Event names in registration order, reserved events last. */
  events: ReadonlyMap<StructureEventName, VocabularyEvent>;
}

/** Pattern of AOI kinds and event names. */
export const NAME_PATTERN = /^[A-Z][A-Z0-9_]{0,47}$/;

/** Pattern of indicator plugin ids. */
export const PLUGIN_ID_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;

/**
 * Events any AOI-publishing plugin may emit, with their default labels;
 * they never add a plugin dependency unless a rule names a source.
 */
export const RESERVED_EVENTS: Readonly<Record<string, string>> = {
  AOI_INVALIDATED: 'AOI invalidated',
  AOI_EXPIRED: 'AOI expired',
};

/** Vocabulary with no plugins (engine not reachable yet). */
export const EMPTY_VOCABULARY: Vocabulary = {
  plugins: new Map(),
  kinds: new Map(),
  events: new Map(),
};

/**
 * Builds the vocabulary from plugin descriptors.
 * @param infos Plugin descriptors in registration order, as the engine
 *     lists them.
 * @return Vocabulary. A kind published twice keeps its first publisher;
 *     reserved events are listed only when some plugin publishes AOIs.
 */
export function buildVocabulary(
  infos: readonly IndicatorPluginInfo[]
): Vocabulary {
  const plugins = new Map<string, IndicatorPluginInfo>();
  const kinds = new Map<AoiKind, VocabularyKind>();
  const events = new Map<StructureEventName, VocabularyEvent>();
  const zonePlugins: string[] = [];
  for (const info of infos) {
    if (plugins.has(info.plugin_id)) continue;
    plugins.set(info.plugin_id, info);
    if (info.output_kinds.length > 0) zonePlugins.push(info.plugin_id);
    for (const kind of info.output_kinds) {
      if (kinds.has(kind)) continue;
      const style = info.display.kinds.find(s => s.kind === kind);
      kinds.set(kind, {pluginId: info.plugin_id, label: style?.label ?? kind});
    }
    for (const name of info.output_events) {
      if (name in RESERVED_EVENTS) continue;
      const style = info.display.events.find(s => s.name === name);
      const known = events.get(name);
      events.set(name, {
        pluginIds: [...(known?.pluginIds ?? []), info.plugin_id],
        label:
          known && known.label !== name ? known.label : (style?.label ?? name),
      });
    }
  }
  if (zonePlugins.length > 0) {
    for (const [name, fallback] of Object.entries(RESERVED_EVENTS)) {
      const style = zonePlugins
        .map(id => plugins.get(id)?.display.events.find(s => s.name === name))
        .find(s => s !== undefined);
      events.set(name, {
        pluginIds: zonePlugins,
        label: style?.label ?? fallback,
      });
    }
  }
  return {plugins, kinds, events};
}

/**
 * Returns the display label of an AOI kind.
 * @param vocab Vocabulary.
 * @param kind AOI kind.
 * @return Plugin label, or the raw kind when no installed plugin labels it.
 */
export function kindLabel(vocab: Vocabulary, kind: AoiKind): string {
  return vocab.kinds.get(kind)?.label ?? kind;
}

/**
 * Returns the display label of an event.
 * @param vocab Vocabulary.
 * @param name Event name.
 * @return Plugin label, the reserved event's default label, or the raw name.
 */
export function eventLabel(
  vocab: Vocabulary,
  name: StructureEventName
): string {
  return vocab.events.get(name)?.label ?? RESERVED_EVENTS[name] ?? name;
}

/**
 * Returns the display name of a plugin.
 * @param vocab Vocabulary.
 * @param pluginId Plugin id.
 * @return Display name, or the id when the plugin is not installed.
 */
export function pluginName(vocab: Vocabulary, pluginId: string): string {
  return vocab.plugins.get(pluginId)?.display_name ?? pluginId;
}

/**
 * Lists the values of a plugin a STATE condition can match: strings and
 * booleans.
 * @param info Plugin descriptor.
 * @return Value specs in declaration order.
 */
export function stateValues(info: IndicatorPluginInfo): ValueSpec[] {
  return info.output_values.filter(
    v => v.type === 'string' || v.type === 'boolean'
  );
}
