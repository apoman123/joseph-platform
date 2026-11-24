/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Maps backend indicator records to chart overlay models.
 *
 * Render-only: every value comes from the Python engine's `AoiRecord` (area
 * of interest, AOI), `StructureEvent` and `IndicatorSnapshot` outputs, and
 * every style comes from the publishing plugin's `PluginDisplay` metadata
 * (`IndicatorPluginInfo.display`). Nothing here derives a trading signal
 * from candles or knows any plugin by name: kinds styled `box` become
 * zones, kinds styled `level` become rays from their creation time, and
 * events become bar markers and, when styled so, a segment from the time a
 * `meta` key names to the event bar. Kinds and events without a style fall
 * back to neutral defaults. Prices stay contract decimal strings.
 */

import type {
  BoxOverlay,
  MarkerOverlay,
  OverlayLayer,
  OverlayLineStyle,
  OverlayModel,
  OverlayTone,
  PriceLineOverlay,
} from '@joseph/chart-core';
import type {
  AoiKind,
  AoiRecord,
  DisplayTone,
  EndReasonStyle,
  EventStyle,
  FeedState,
  IndicatorPluginInfo,
  IndicatorSnapshot,
  InstrumentId,
  KindStyle,
  StructureEvent,
  StructureEventName,
  Timeframe,
} from '@joseph/contracts';

/** Event names any plugin may publish without declaring a style. */
const RESERVED_EVENTS: ReadonlySet<StructureEventName> = new Set([
  'AOI_INVALIDATED',
  'AOI_EXPIRED',
]);

/** Paint order of a layer whose plugin is not in the catalogue. */
const DEFAULT_Z_INDEX = 50;

/** Fill opacity of a live zone whose style gives none. */
const DEFAULT_FILL_OPACITY = 0.1;

/** Per-plugin visibility and kind filters. */
export interface PluginVizSettings {
  /** Whether the plugin layer is shown. */
  visible: boolean;
  /** Kinds hidden by the user. */
  hiddenKinds: readonly AoiKind[];
  /** Whether event markers are shown. */
  showEvents: boolean;
  /** When set, only these kinds are shown (per-chart kind filter). */
  shownKinds?: readonly AoiKind[];
  /**
   * User colors per AOI kind, event name or plotted value key (`#rrggbb`).
   */
  colors?: Readonly<Record<string, string>>;
}

/** Visualization settings (persisted per user). */
export interface VizSettings {
  /**
   * Settings per plugin id; a plugin without an entry is visible and shows
   * events as its display metadata says.
   */
  plugins: Readonly<Record<string, PluginVizSettings>>;
  /** Show invalidated and expired zones as faded outlines. */
  showEnded: boolean;
  /**
   * Most recently confirmed zones drawn per plugin, kind and side (0 or less
   * draws all; unset uses DEFAULT_MAX_ZONES_PER_KIND). Display-only
   * decluttering; strategies still evaluate every zone.
   */
  maxZonesPerKind?: number;
}

/** Default cap of zones drawn per plugin, kind and side. */
export const DEFAULT_MAX_ZONES_PER_KIND = 4;

/**
 * Builds default settings for a plugin catalogue: every plugin visible,
 * event markers as each plugin's `display.show_events` says, ended zones
 * hidden.
 * @param infos Plugin descriptors.
 * @return Settings.
 */
export function defaultVizSettings(
  infos: readonly IndicatorPluginInfo[]
): VizSettings {
  return {
    plugins: Object.fromEntries(
      infos.map(info => [
        info.plugin_id,
        {visible: true, hiddenKinds: [], showEvents: info.display.show_events},
      ])
    ),
    showEnded: false,
    maxZonesPerKind: DEFAULT_MAX_ZONES_PER_KIND,
  };
}

/** Defaults without a catalogue: no plugin entries, ended zones hidden. */
export const DEFAULT_VIZ_SETTINGS: VizSettings = defaultVizSettings([]);

/**
 * Indexes plugin descriptors by id.
 * @param infos Plugin descriptors.
 * @return Map from plugin id to descriptor.
 */
export function pluginMap(
  infos: readonly IndicatorPluginInfo[]
): Map<string, IndicatorPluginInfo> {
  return new Map(infos.map(info => [info.plugin_id, info]));
}

/**
 * Returns the plugins listed in the Layers menu, in registration order.
 * @param infos Plugin descriptors in registration order.
 * @return Plugins whose `display.layer` is set.
 */
export function layerPlugins(
  infos: readonly IndicatorPluginInfo[]
): IndicatorPluginInfo[] {
  return infos.filter(info => info.display.layer);
}

/**
 * Returns whether a plugin publishes anything the overlay layers draw.
 * @param info Plugin descriptor.
 * @return True when it is a layer and declares an AOI kind or an event.
 */
export function drawsRecords(info: IndicatorPluginInfo): boolean {
  return (
    info.display.layer &&
    (info.output_kinds.length > 0 || info.output_events.length > 0)
  );
}

/**
 * Returns the style a plugin declares for an AOI kind.
 * @param info Plugin descriptor (undefined when unknown).
 * @param kind AOI kind.
 * @return Style, or undefined when the plugin declares none.
 */
export function kindStyle(
  info: IndicatorPluginInfo | undefined,
  kind: AoiKind
): KindStyle | undefined {
  return info?.display.kinds.find(style => style.kind === kind);
}

/**
 * Returns the style a plugin declares for an event name.
 * @param info Plugin descriptor (undefined when unknown).
 * @param name Event name.
 * @return Style, or undefined when the plugin declares none.
 */
export function eventStyle(
  info: IndicatorPluginInfo | undefined,
  name: StructureEventName
): EventStyle | undefined {
  return info?.display.events.find(style => style.name === name);
}

/**
 * Returns the display label of an AOI kind.
 * @param info Plugin descriptor (undefined when unknown).
 * @param kind AOI kind.
 * @return The style label, or the kind itself.
 */
export function kindLabel(
  info: IndicatorPluginInfo | undefined,
  kind: AoiKind
): string {
  return kindStyle(info, kind)?.label ?? kind;
}

/**
 * Returns the display label of an event name.
 * @param info Plugin descriptor (undefined when unknown).
 * @param name Event name.
 * @return The style label, or the name itself.
 */
export function eventLabel(
  info: IndicatorPluginInfo | undefined,
  name: StructureEventName
): string {
  return eventStyle(info, name)?.label ?? name;
}

/**
 * Returns whether events of a name are drawn at all.
 * @param style Declared style (undefined when the plugin declares none).
 * @param name Event name.
 * @return False for `render: 'none'` and for unstyled reserved events
 *     (`AOI_INVALIDATED`, `AOI_EXPIRED`), whose effect the AOI state shows.
 */
export function eventDrawn(
  style: EventStyle | undefined,
  name: StructureEventName
): boolean {
  if (!style) return !RESERVED_EVENTS.has(name);
  return style.render !== 'none';
}

/**
 * Resolves a display tone to an overlay tone.
 * @param tone Display tone from the plugin metadata.
 * @param subject AOI (for `side`), a plotted value (for `sign`) or null.
 * @return `side` follows the AOI side (bullish bid, bearish ask, else
 *     neutral); `sign` follows the value's sign (bid at or above zero, ask
 *     below, neutral without a value); `up` is bid, `down` ask and
 *     `accent` the user color; the rest map to the tone of the same name.
 */
export function toneOf(
  tone: DisplayTone,
  subject: Pick<AoiRecord, 'side'> | number | null
): OverlayTone {
  switch (tone) {
    case 'side': {
      const side = typeof subject === 'object' ? subject?.side : undefined;
      if (side === 'bullish') return 'bid';
      return side === 'bearish' ? 'ask' : 'neutral';
    }
    case 'sign':
      if (typeof subject !== 'number') return 'neutral';
      return subject >= 0 ? 'bid' : 'ask';
    case 'up':
      return 'bid';
    case 'down':
      return 'ask';
    case 'accent':
      return 'user';
    case 'warning':
      return 'warning';
    case 'muted':
      return 'muted';
    case 'neutral':
    default:
      return 'neutral';
  }
}

/**
 * Returns whether an AOI's state means it no longer reacts to price.
 * @param aoi AOI record.
 * @return True for invalidated and expired.
 */
export function isEnded(aoi: AoiRecord): boolean {
  return aoi.state !== 'active' && aoi.state !== 'tested';
}

/**
 * Returns the overlay tone of an AOI: muted once ended, otherwise its
 * style's tone (by side when the style gives none).
 * @param aoi AOI record.
 * @param style Kind style, if the plugin declares one.
 * @return Overlay tone.
 */
export function aoiTone(aoi: AoiRecord, style?: KindStyle): OverlayTone {
  if (isEnded(aoi)) return 'muted';
  return toneOf(style?.tone ?? 'side', aoi);
}

/**
 * Returns a short label for an AOI: kind label, quality score and any
 * non-active state, using the plugin's end-reason label when it gives one.
 * @param aoi AOI record.
 * @param style Kind style, if the plugin declares one.
 * @param endReasons End-reason labels of the plugin.
 * @return Label such as `S1`, `Zone 76, tested` or `Zone 76, broken`.
 */
export function aoiLabel(
  aoi: AoiRecord,
  style?: KindStyle,
  endReasons: readonly EndReasonStyle[] = []
): string {
  const base = style?.label ?? aoi.kind;
  const score = aoi.quality_score != null ? ` ${aoi.quality_score}` : '';
  if (aoi.state === 'tested') return `${base}${score}, tested`;
  if (!isEnded(aoi)) return `${base}${score}`;
  const reason = endReasons.find(r => r.reason === aoi.end_reason)?.label;
  return `${base}${score}, ${reason ?? aoi.state}`;
}

/**
 * Returns a style's default color when it is a valid `#rrggbb`.
 * @param color Color from the metadata.
 * @return The color, or undefined.
 */
function styleColor(color: string | null | undefined): string | undefined {
  return color && /^#[0-9a-f]{6}$/i.test(color) ? color : undefined;
}

/**
 * Maps one AOI to an overlay model. A `level` kind becomes a ray at `lower`
 * from `created_at_ms`, dashed and pending while live when the style says
 * `pending_until_ended`; anything else (including kinds without a style)
 * becomes a box from `created_at_ms` to `invalidated_at_ms` (open to the
 * right while unset). Ended AOIs are dotted and faint, tested zones
 * lighter. A style color is the default color of live AOIs.
 * @param aoi AOI record.
 * @param style Kind style, if the plugin declares one.
 * @param endReasons End-reason labels of the plugin.
 * @return Box or ray model.
 */
export function aoiToOverlay(
  aoi: AoiRecord,
  style?: KindStyle,
  endReasons: readonly EndReasonStyle[] = []
): BoxOverlay | PriceLineOverlay {
  const ended = isEnded(aoi);
  const tone = aoiTone(aoi, style);
  const label = aoiLabel(aoi, style, endReasons);
  const color = ended ? undefined : styleColor(style?.color);
  const lineStyle: OverlayLineStyle = style?.line_style ?? 'solid';
  if (style?.render === 'level') {
    const pending = style.pending_until_ended === true && !ended;
    return {
      kind: 'price-line',
      id: aoi.aoi_id,
      price: aoi.lower,
      tone,
      lineStyle: ended ? 'dotted' : pending ? 'dashed' : lineStyle,
      label,
      fromMs: aoi.created_at_ms,
      ...(pending ? {pending: true} : {}),
      ...(color ? {color} : {}),
    };
  }
  const fill = style?.fill_opacity ?? DEFAULT_FILL_OPACITY;
  return {
    kind: 'box',
    id: aoi.aoi_id,
    fromMs: aoi.created_at_ms,
    toMs: aoi.invalidated_at_ms ?? null,
    lower: aoi.lower,
    upper: aoi.upper,
    tone,
    fillOpacity: ended ? 0.04 : fill * (aoi.state === 'tested' ? 0.6 : 1),
    lineStyle: ended ? 'dotted' : lineStyle,
    label,
    midline: style?.midline === true,
    ...(color ? {color} : {}),
  };
}

/**
 * Reads a UTC ms time from an event's `meta`.
 * @param event Event.
 * @param key `meta` key, e.g. the style's `time_meta`.
 * @return Time (UTC ms), or null when missing or not a finite number.
 */
export function eventMetaMs(
  event: StructureEvent,
  key: string | null | undefined
): number | null {
  if (!key) return null;
  // The contract types `meta` as an open object (`{}`); read it as a record
  // and check the value's type below.
  const value = (event.meta as Record<string, unknown> | undefined)?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * Maps an event to a marker. Without a style it is a neutral circle above
 * the bar labelled with the event name; a style sets the label, placement,
 * glyph and tone, and its `time_meta` moves the marker to the bar that
 * `meta` key names.
 * @param event Event.
 * @param style Event style, if the plugin declares one.
 * @return Marker, or null when the event is not drawn as a marker.
 */
export function eventToMarker(
  event: StructureEvent,
  style?: EventStyle
): MarkerOverlay | null {
  if (!eventDrawn(style, event.name)) return null;
  if (style?.render === 'segment') return null;
  const color = styleColor(style?.color);
  return {
    kind: 'marker',
    id: event.event_id,
    timeMs: eventMetaMs(event, style?.time_meta) ?? event.bar_start_ms,
    position: style?.position ?? 'above',
    shape: style?.shape ?? 'circle',
    tone: toneOf(style?.tone ?? 'neutral', null),
    text: style?.label ?? event.name,
    ...(color ? {color} : {}),
  };
}

/**
 * Maps an event styled `segment` or `marker_and_segment` to a line at its
 * price from the time its `time_meta` key names to the event bar.
 * @param event Event.
 * @param style Event style, if the plugin declares one.
 * @return Line-shaped box, or null without such a style, without a price,
 *     or when the start time is missing or not before the event bar.
 */
export function eventToSegment(
  event: StructureEvent,
  style?: EventStyle
): BoxOverlay | null {
  if (style?.render !== 'segment' && style?.render !== 'marker_and_segment') {
    return null;
  }
  if (!event.price) return null;
  const fromMs = eventMetaMs(event, style.time_meta);
  if (fromMs === null || fromMs >= event.bar_start_ms) return null;
  const color = styleColor(style.color);
  // A zero-height box is a segment with both ends fixed in time; a price
  // line could only run from the start to the right edge.
  return {
    kind: 'box',
    id: `${event.event_id}:segment`,
    fromMs,
    toMs: event.bar_start_ms,
    lower: event.price,
    upper: event.price,
    tone: toneOf(style.tone ?? 'neutral', null),
    fillOpacity: 0,
    lineStyle: style.line_style ?? 'solid',
    ...(color ? {color} : {}),
  };
}

/**
 * Applies a user color to a model when one is configured for its key.
 * @param model Overlay model.
 * @param key AOI kind or event name.
 * @param colors Configured colors.
 * @return A colored copy when a valid `#rrggbb` color is configured,
 *     otherwise the model itself.
 */
function colored<T extends OverlayModel>(
  model: T,
  key: string,
  colors: Readonly<Record<string, string>> | undefined
): T {
  const color = styleColor(colors?.[key]);
  return color ? {...model, color} : model;
}

/** Inputs for building indicator layers on one chart. */
export interface IndicatorLayerInput {
  /** Instrument shown on the chart. */
  instrumentId: InstrumentId;
  /** Timeframe shown on the chart. */
  timeframe: Timeframe;
  /** AOI records from `/v1/aois`. */
  aois: readonly AoiRecord[];
  /** Events (from `/v1/aois` or snapshots). */
  events: readonly StructureEvent[];
  /** Time (UTC ms) the backend computed the records, shown in captions. */
  asOfMs: number;
  /** Provenance of the backend data. */
  feedState: FeedState;
  /** User settings. */
  settings: VizSettings;
  /**
   * Plugin descriptors by id (styles, names, paint order); records of a
   * plugin missing here are drawn with the defaults.
   */
  plugins: ReadonlyMap<string, IndicatorPluginInfo>;
  /** Replay time (UTC ms); when set, captions show it and say `replay`. */
  atMs?: number | null;
}

/**
 * Returns the caption label of a feed state.
 * @param state Feed state.
 * @return Label such as `live` or `demo data`.
 */
export function feedStateLabel(state: FeedState): string {
  const labels: Record<FeedState, string> = {
    LIVE: 'live',
    POLL: 'polled',
    REPLAY: 'replay',
    STALE: 'stale',
    DEMO: 'demo data',
    UNAVAILABLE: 'unavailable',
    CONNECTING: 'connecting',
  };
  return labels[state];
}

/**
 * Keeps the most recently confirmed zones per plugin, kind and side.
 * @param aois Candidate AOIs.
 * @param max Maximum per group; 0 or less keeps everything.
 * @return Kept AOIs in their original order.
 */
export function limitPerKind(
  aois: readonly AoiRecord[],
  max: number
): AoiRecord[] {
  if (max <= 0) return [...aois];
  const groups = new Map<string, AoiRecord[]>();
  for (const aoi of aois) {
    const key = `${aoi.source}|${aoi.kind}|${aoi.side}`;
    const list = groups.get(key) ?? [];
    list.push(aoi);
    groups.set(key, list);
  }
  const kept = new Set<AoiRecord>();
  for (const list of groups.values()) {
    const newest = [...list]
      .sort((a, b) => b.confirmed_at_ms - a.confirmed_at_ms)
      .slice(0, max);
    for (const aoi of newest) {
      kept.add(aoi);
    }
  }
  return aois.filter(aoi => kept.has(aoi));
}

/**
 * Returns whether a plugin's event markers are shown.
 * @param settings User settings.
 * @param info Plugin descriptor (undefined when unknown).
 * @param pluginId Plugin id.
 * @return The plugin's setting when present, else its metadata default
 *     (true for an unknown plugin).
 */
function eventsShown(
  settings: VizSettings,
  info: IndicatorPluginInfo | undefined,
  pluginId: string
): boolean {
  return (
    settings.plugins[pluginId]?.showEvents ?? info?.display.show_events ?? true
  );
}

/**
 * Builds one overlay layer per indicator plugin from its display metadata.
 * Records for any other instrument are dropped, so a venue switch can never
 * show the previous venue's zones.
 * @param input Records, settings and plugin descriptors.
 * @return Layers keyed `indicator-viz:{plugin_id}`, one for each plugin
 *     with at least one model to draw, painted in `display.z_index` order,
 *     each with a provenance caption.
 */
export function buildIndicatorLayers(
  input: IndicatorLayerInput
): OverlayLayer[] {
  const byPlugin = new Map<string, OverlayModel[]>();
  /**
   * Appends a model to its plugin's layer.
   * @param plugin Plugin id.
   * @param model Model.
   */
  function push(plugin: string, model: OverlayModel): void {
    const list = byPlugin.get(plugin) ?? [];
    list.push(model);
    byPlugin.set(plugin, list);
  }
  const shown = input.aois.filter(aoi => {
    if (aoi.instrument_id !== input.instrumentId) return false;
    const plugin = input.settings.plugins[aoi.source];
    if (plugin && (!plugin.visible || plugin.hiddenKinds.includes(aoi.kind))) {
      return false;
    }
    if (plugin?.shownKinds && !plugin.shownKinds.includes(aoi.kind)) {
      return false;
    }
    return !isEnded(aoi) || input.settings.showEnded;
  });
  for (const aoi of limitPerKind(
    shown,
    input.settings.maxZonesPerKind ?? DEFAULT_MAX_ZONES_PER_KIND
  )) {
    const info = input.plugins.get(aoi.source);
    const model = aoiToOverlay(
      aoi,
      kindStyle(info, aoi.kind),
      info?.display.end_reasons
    );
    const colors = input.settings.plugins[aoi.source]?.colors;
    push(aoi.source, colored(model, aoi.kind, colors));
  }
  for (const event of input.events) {
    if (event.instrument_id !== input.instrumentId) continue;
    const plugin = input.settings.plugins[event.source];
    const info = input.plugins.get(event.source);
    if (plugin && !plugin.visible) continue;
    if (!eventsShown(input.settings, info, event.source)) continue;
    const style = eventStyle(info, event.name);
    const marker = eventToMarker(event, style);
    if (marker) push(event.source, colored(marker, event.name, plugin?.colors));
    const segment = eventToSegment(event, style);
    if (segment) {
      push(event.source, colored(segment, event.name, plugin?.colors));
    }
  }
  const time = new Date(input.atMs ?? input.asOfMs).toISOString().slice(11, 16);
  const provenance =
    input.atMs != null ? 'replay' : feedStateLabel(input.feedState);
  return [...byPlugin.entries()].map(([plugin, models]) => {
    const info = input.plugins.get(plugin);
    return {
      layerId: `indicator-viz:${plugin}`,
      zIndex: info?.display.z_index ?? DEFAULT_Z_INDEX,
      visible: true,
      models,
      caption: `${info?.display_name ?? plugin}, as of ${time} UTC (${provenance})`,
    };
  });
}

/**
 * Extracts AOIs and events from snapshot responses.
 * @param snapshots Indicator snapshots.
 * @return AOIs and events.
 */
export function recordsFromSnapshots(snapshots: readonly IndicatorSnapshot[]): {
  aois: AoiRecord[];
  events: StructureEvent[];
} {
  return {
    aois: snapshots.flatMap(snapshot => snapshot.aois),
    events: snapshots.flatMap(snapshot => snapshot.events),
  };
}
