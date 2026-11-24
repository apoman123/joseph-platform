/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Maps backend indicator records to chart overlay models.
 *
 * Render-only: every value comes from the Python engine's `AoiRecord`,
 * `StructureEvent` and `IndicatorSnapshot` outputs. Nothing here derives a
 * trading signal from candles.
 */

import type {
  BoxOverlay,
  MarkerOverlay,
  OverlayLayer,
  OverlayModel,
  OverlayTone,
  PriceLineOverlay,
} from '@joseph/chart-core';
import type {
  AoiKind,
  AoiRecord,
  FeedState,
  IndicatorSnapshot,
  InstrumentId,
  StructureEvent,
  StructureEventName,
  Timeframe,
} from '@joseph/contracts';

/** Display names of the first-party indicator plugins. */
export const PLUGIN_NAMES: Readonly<Record<string, string>> = {
  daily_bias: 'Daily bias',
  fvg_ifvg: 'FVG and IFVG',
  order_breaker_blocks: 'Order and breaker blocks',
  liquidity_sweeps: 'Liquidity',
  order_flow_pressure: 'Order flow',
  auction_value_areas: 'Value areas',
  premium_discount_ote: 'Premium, discount and OTE',
  market_structure: 'Market structure',
};

/** Paint order per plugin (zones below lines below markers). */
const PLUGIN_Z: Readonly<Record<string, number>> = {
  premium_discount_ote: 10,
  auction_value_areas: 20,
  order_breaker_blocks: 30,
  fvg_ifvg: 40,
  liquidity_sweeps: 50,
  daily_bias: 60,
  market_structure: 65,
  order_flow_pressure: 70,
};

/** AOI kinds drawn as horizontal levels rather than zones. */
const LEVEL_KINDS: ReadonlySet<AoiKind> = new Set([
  'BSL',
  'SSL',
  'EQH',
  'EQL',
  'VAH',
  'POC',
  'VAL',
  'PDH',
  'PDL',
  'SESSION_HIGH',
  'SESSION_LOW',
  'SWING_HIGH',
  'SWING_LOW',
]);

/** Kinds that are pending levels (drawn dashed until broken). */
const PENDING_KINDS: ReadonlySet<AoiKind> = new Set([
  'SWING_HIGH',
  'SWING_LOW',
]);

/** Fill opacity per zone kind. */
const FILL: Partial<Record<AoiKind, number>> = {
  FVG: 0.14,
  IFVG: 0.1,
  OB: 0.18,
  BREAKER: 0.12,
  PREMIUM: 0.05,
  DISCOUNT: 0.05,
  EQUILIBRIUM: 0.04,
  OTE: 0.1,
};

/** Per-plugin visibility and kind filters. */
export interface PluginVizSettings {
  /** Whether the plugin layer is shown. */
  visible: boolean;
  /** Kinds hidden by the user. */
  hiddenKinds: readonly AoiKind[];
  /** Whether structure-event markers are shown. */
  showEvents: boolean;
  /** When set, only these kinds are shown (per-chart kind filter). */
  shownKinds?: readonly AoiKind[];
  /** User colors per AOI kind or event name (`#rrggbb`). */
  colors?: Readonly<Record<string, string>>;
}

/** Visualization settings (persisted per user). */
export interface VizSettings {
  /** Settings per plugin id. */
  plugins: Readonly<Record<string, PluginVizSettings>>;
  /** Show invalidated/swept/expired zones as faded outlines. */
  showEnded: boolean;
  /**
   * Most recently confirmed zones drawn per plugin, kind and side (0 = all).
   * Display-only decluttering; strategies still evaluate every zone.
   */
  maxZonesPerKind?: number;
}

/** Default cap of zones drawn per plugin, kind and side. */
export const DEFAULT_MAX_ZONES_PER_KIND = 4;

/** Defaults: every plugin visible, ended zones hidden. */
export const DEFAULT_VIZ_SETTINGS: VizSettings = {
  plugins: Object.fromEntries(
    Object.keys(PLUGIN_NAMES).map(id => [
      id,
      {visible: true, hiddenKinds: [], showEvents: true},
    ])
  ),
  showEnded: false,
  maxZonesPerKind: DEFAULT_MAX_ZONES_PER_KIND,
};

/**
 * Whether an AOI state means the zone no longer reacts to price.
 * @param aoi AOI record.
 * @return True for inverted, swept, invalidated and expired.
 */
export function isEnded(aoi: AoiRecord): boolean {
  return aoi.state !== 'active' && aoi.state !== 'tested';
}

/**
 * Tone for an AOI.
 * @param aoi AOI record.
 * @return Overlay tone.
 */
export function aoiTone(aoi: AoiRecord): OverlayTone {
  if (isEnded(aoi)) return 'muted';
  if (aoi.kind === 'PREMIUM') return 'ask';
  if (aoi.kind === 'DISCOUNT') return 'bid';
  if (aoi.kind === 'EQUILIBRIUM' || aoi.kind === 'POC') return 'neutral';
  if (aoi.side === 'bullish') return 'bid';
  if (aoi.side === 'bearish') return 'ask';
  return 'neutral';
}

/**
 * Short label for an AOI.
 * @param aoi AOI record.
 * @return Label such as `FVG 76` or `OTE (range 9f3a)`.
 */
export function aoiLabel(aoi: AoiRecord): string {
  const names: Partial<Record<AoiKind, string>> = {
    PREMIUM: 'Premium',
    DISCOUNT: 'Discount',
    EQUILIBRIUM: 'EQ',
    SESSION_HIGH: 'Session high',
    SESSION_LOW: 'Session low',
    SWING_HIGH: 'Swing high',
    SWING_LOW: 'Swing low',
  };
  const base = names[aoi.kind] ?? aoi.kind;
  const score = aoi.quality_score != null ? ` ${aoi.quality_score}` : '';
  const range = aoi.range_id ? ` (range ${aoi.range_id.slice(-4)})` : '';
  // Unbroken swings are drawn dashed; the line style carries "pending" so
  // the in-chart label stays short next to liquidity labels.
  const state =
    aoi.state === 'tested' ? ', tested' : isEnded(aoi) ? `, ${aoi.state}` : '';
  return `${base}${score}${range}${state}`;
}

/**
 * Maps one AOI to an overlay model.
 * @param aoi AOI record.
 * @return Box or ray model.
 */
export function aoiToOverlay(aoi: AoiRecord): BoxOverlay | PriceLineOverlay {
  const tone = aoiTone(aoi);
  if (LEVEL_KINDS.has(aoi.kind)) {
    return {
      kind: 'price-line',
      id: aoi.aoi_id,
      price: aoi.lower,
      tone,
      lineStyle: isEnded(aoi)
        ? 'dotted'
        : aoi.kind === 'POC'
          ? 'solid'
          : 'dashed',
      label: aoiLabel(aoi),
      fromMs: aoi.created_at_ms,
      ...(PENDING_KINDS.has(aoi.kind) && !isEnded(aoi) ? {pending: true} : {}),
    };
  }
  return {
    kind: 'box',
    id: aoi.aoi_id,
    fromMs: aoi.created_at_ms,
    toMs: aoi.invalidated_at_ms ?? null,
    lower: aoi.lower,
    upper: aoi.upper,
    tone,
    fillOpacity: isEnded(aoi)
      ? 0.04
      : (FILL[aoi.kind] ?? 0.1) * (aoi.state === 'tested' ? 0.6 : 1),
    lineStyle: isEnded(aoi)
      ? 'dotted'
      : aoi.kind === 'BREAKER' || aoi.kind === 'IFVG'
        ? 'dashed'
        : 'solid',
    label: aoiLabel(aoi),
    midline: aoi.kind === 'FVG' || aoi.kind === 'IFVG',
  };
}

/** Marker presentation per event name. */
const EVENT_MARKERS: Partial<
  Record<
    StructureEventName,
    Pick<MarkerOverlay, 'position' | 'shape' | 'tone' | 'text'>
  >
> = {
  BOS_BULL: {position: 'below', shape: 'arrow-up', tone: 'bid', text: 'BOS'},
  BOS_BEAR: {position: 'above', shape: 'arrow-down', tone: 'ask', text: 'BOS'},
  CHOCH_BULL: {
    position: 'below',
    shape: 'arrow-up',
    tone: 'bid',
    text: 'CHoCH',
  },
  CHOCH_BEAR: {
    position: 'above',
    shape: 'arrow-down',
    tone: 'ask',
    text: 'CHoCH',
  },
  SSL_SWEEP: {
    position: 'below',
    shape: 'circle',
    tone: 'bid',
    text: 'SSL sweep',
  },
  BSL_SWEEP: {
    position: 'above',
    shape: 'circle',
    tone: 'ask',
    text: 'BSL sweep',
  },
  SELLER_FAILURE: {
    position: 'below',
    shape: 'arrow-up',
    tone: 'bid',
    text: 'Seller failure',
  },
  BUYER_FAILURE: {
    position: 'above',
    shape: 'arrow-down',
    tone: 'ask',
    text: 'Buyer failure',
  },
  BUYER_BREAKOUT: {
    position: 'below',
    shape: 'square',
    tone: 'bid',
    text: 'Buyer breakout',
  },
  SELLER_BREAKOUT: {
    position: 'above',
    shape: 'square',
    tone: 'ask',
    text: 'Seller breakout',
  },
  INITIAL_BREAK_BULL: {
    position: 'below',
    shape: 'arrow-up',
    tone: 'bid',
    text: 'Initial break',
  },
  INITIAL_BREAK_BEAR: {
    position: 'above',
    shape: 'arrow-down',
    tone: 'ask',
    text: 'Initial break',
  },
  SWING_HH: {position: 'above', shape: 'circle', tone: 'bid', text: 'HH'},
  SWING_LH: {position: 'above', shape: 'circle', tone: 'ask', text: 'LH'},
  SWING_EQH: {position: 'above', shape: 'circle', tone: 'neutral', text: 'EQH'},
  SWING_HL: {position: 'below', shape: 'circle', tone: 'bid', text: 'HL'},
  SWING_LL: {position: 'below', shape: 'circle', tone: 'ask', text: 'LL'},
  SWING_EQL: {position: 'below', shape: 'circle', tone: 'neutral', text: 'EQL'},
};

/**
 * Short marker text of an event name, if the event is drawn as a marker.
 * @param name Event name.
 * @return Marker text or null.
 */
export function markerText(name: StructureEventName): string | null {
  return EVENT_MARKERS[name]?.text ?? null;
}

/** Events whose break line runs from the broken pivot to the break bar. */
const BREAK_EVENTS: ReadonlySet<StructureEventName> = new Set([
  'BOS_BULL',
  'BOS_BEAR',
  'CHOCH_BULL',
  'CHOCH_BEAR',
  'INITIAL_BREAK_BULL',
  'INITIAL_BREAK_BEAR',
]);

/** Swing label events (drawn at the pivot bar once confirmed). */
const SWING_EVENTS: ReadonlySet<StructureEventName> = new Set([
  'SWING_HH',
  'SWING_HL',
  'SWING_LH',
  'SWING_LL',
  'SWING_EQH',
  'SWING_EQL',
]);

/**
 * Reads the pivot time a structure event refers to, if the engine sent one.
 * @param event Structure event.
 * @return Pivot bar start (UTC ms) or null.
 */
export function eventPivotMs(event: StructureEvent): number | null {
  const pivot = (event.meta as Record<string, unknown> | undefined)
    ?.pivot_time_ms;
  return typeof pivot === 'number' && Number.isFinite(pivot) ? pivot : null;
}

/**
 * Maps a structure event to a marker, if it has one.
 * @param event Structure event.
 * @return Marker or null.
 */
export function eventToMarker(event: StructureEvent): MarkerOverlay | null {
  const style = EVENT_MARKERS[event.name];
  if (!style) return null;
  const mss =
    event.source === 'market_structure' &&
    (event.name === 'CHOCH_BULL' || event.name === 'CHOCH_BEAR');
  const pivot = SWING_EVENTS.has(event.name) ? eventPivotMs(event) : null;
  return {
    kind: 'marker',
    id: event.event_id,
    timeMs: pivot ?? event.bar_start_ms,
    ...style,
    ...(mss ? {text: 'MSS'} : {}),
  };
}

/**
 * Maps a break event to the line from the broken pivot to the break bar.
 * @param event Structure event.
 * @return Line-shaped box, or null without pivot time or price.
 */
export function eventToBreakLine(event: StructureEvent): BoxOverlay | null {
  if (!BREAK_EVENTS.has(event.name) || !event.price) return null;
  const pivot = eventPivotMs(event);
  if (pivot === null || pivot >= event.bar_start_ms) return null;
  const bullish = event.name.endsWith('_BULL');
  return {
    kind: 'box',
    id: `${event.event_id}:line`,
    fromMs: pivot,
    toMs: event.bar_start_ms,
    lower: event.price,
    upper: event.price,
    tone: bullish ? 'bid' : 'ask',
    fillOpacity: 0,
    lineStyle: event.name.startsWith('BOS') ? 'solid' : 'dashed',
  };
}

/**
 * Applies a user color to a model when one is configured for its key.
 * @param model Overlay model.
 * @param key AOI kind or event name.
 * @param colors Configured colors.
 * @return Model, colored when configured.
 */
function colored<T extends OverlayModel>(
  model: T,
  key: string,
  colors: Readonly<Record<string, string>> | undefined
): T {
  const color = colors?.[key];
  return color && /^#[0-9a-f]{6}$/i.test(color) ? {...model, color} : model;
}

/** Inputs for building indicator layers on one chart. */
export interface IndicatorLayerInput {
  /** Instrument shown on the chart. */
  instrumentId: InstrumentId;
  /** Timeframe shown on the chart. */
  timeframe: Timeframe;
  /** AOI records from `/v1/aois`. */
  aois: readonly AoiRecord[];
  /** Structure events (from `/v1/aois` or snapshots). */
  events: readonly StructureEvent[];
  /** Backend as-of time. */
  asOfMs: number;
  /** Provenance of the backend data. */
  feedState: FeedState;
  /** User settings. */
  settings: VizSettings;
  /** Replay point-in-time; captions then say `replay`. */
  atMs?: number | null;
}

/**
 * Human label for a feed state in captions.
 * @param state Feed state.
 * @return Label.
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
    [...list]
      .sort((a, b) => b.confirmed_at_ms - a.confirmed_at_ms)
      .slice(0, max)
      .forEach(aoi => kept.add(aoi));
  }
  return aois.filter(aoi => kept.has(aoi));
}

/**
 * Builds one overlay layer per indicator plugin. Records for any other
 * instrument are dropped, so a venue switch can never show the previous
 * venue's zones.
 * @param input Records and settings.
 * @return Layers keyed `indicator-viz:{plugin_id}`.
 */
export function buildIndicatorLayers(
  input: IndicatorLayerInput
): OverlayLayer[] {
  const byPlugin = new Map<string, OverlayModel[]>();
  const push = (plugin: string, model: OverlayModel) => {
    const list = byPlugin.get(plugin) ?? [];
    list.push(model);
    byPlugin.set(plugin, list);
  };
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
    const colors = input.settings.plugins[aoi.source]?.colors;
    push(aoi.source, colored(aoiToOverlay(aoi), aoi.kind, colors));
  }
  // `daily_bias` and `market_structure` run the same Pine break rule on the
  // chart series; draw each break once, preferring the structure layer.
  const structureBreaks = new Set(
    input.events
      .filter(e => e.source === 'market_structure' && BREAK_EVENTS.has(e.name))
      .filter(() => {
        const ms = input.settings.plugins['market_structure'];
        return !ms || (ms.visible && ms.showEvents);
      })
      .map(e => `${e.timeframe}|${e.bar_start_ms}|${e.name}`)
  );
  for (const event of input.events) {
    if (event.instrument_id !== input.instrumentId) continue;
    const plugin = input.settings.plugins[event.source];
    if (plugin && (!plugin.visible || !plugin.showEvents)) continue;
    if (
      event.source === 'daily_bias' &&
      structureBreaks.has(
        `${event.timeframe}|${event.bar_start_ms}|${event.name}`
      )
    ) {
      continue;
    }
    const marker = eventToMarker(event);
    if (marker) push(event.source, colored(marker, event.name, plugin?.colors));
    const line = eventToBreakLine(event);
    if (line) push(event.source, colored(line, event.name, plugin?.colors));
  }
  const time = new Date(input.atMs ?? input.asOfMs).toISOString().slice(11, 16);
  const provenance =
    input.atMs != null ? 'replay' : feedStateLabel(input.feedState);
  return [...byPlugin.entries()].map(([plugin, models]) => ({
    layerId: `indicator-viz:${plugin}`,
    zIndex: PLUGIN_Z[plugin] ?? 80,
    visible: true,
    models,
    caption: `${PLUGIN_NAMES[plugin] ?? plugin}, as of ${time} UTC (${provenance})`,
  }));
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
