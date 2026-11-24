/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Declarative overlay models rendered by the chart.
 *
 * Features (indicator visualization, chart trading, alerts) never touch the
 * chart library. They publish overlay layers made of these plain models and
 * the chart adapter renders them. Prices are contract decimal strings so the
 * values that may later become order parameters are never float-derived.
 */

import type {DecimalString, TimestampMs} from '@joseph/contracts';

/** Line dash style. */
export type OverlayLineStyle = 'solid' | 'dashed' | 'dotted';

/** Visual emphasis hints mapped to design tokens by the renderer. */
export type OverlayTone =
  'bid' | 'ask' | 'user' | 'neutral' | 'warning' | 'danger' | 'muted';

/**
 * Horizontal price line, e.g. an order line, a stop-loss or take-profit
 * level, or a liquidity level.
 */
export interface PriceLineOverlay {
  /** Discriminator. */
  kind: 'price-line';
  /** Stable id unique within the layer. */
  id: string;
  /** Price level. */
  price: DecimalString;
  /** Color role. */
  tone: OverlayTone;
  /** Optional user color (`#rrggbb`) overriding the tone. */
  color?: string;
  /** Dash style. */
  lineStyle: OverlayLineStyle;
  /** Axis/label text. */
  label: string;
  /**
   * Whether the user may drag the line (full-width lines only); a drag
   * emits `overlayDragEnd`.
   */
  draggable?: boolean;
  /** Optional time from which the line starts (ray); full width if unset. */
  fromMs?: TimestampMs;
  /**
   * Optional pending styling (e.g. awaiting the venue's acknowledgement):
   * dashed, and full-width lines get a `· pending` label suffix.
   */
  pending?: boolean;
}

/**
 * Rectangular zone between two prices over a time span (areas of interest,
 * ranges).
 */
export interface BoxOverlay {
  /** Discriminator. */
  kind: 'box';
  /** Stable id unique within the layer. */
  id: string;
  /** Left edge (UTC ms). */
  fromMs: TimestampMs;
  /** Right edge (UTC ms); `null` extends to the right edge of the plot. */
  toMs: TimestampMs | null;
  /** Lower price. */
  lower: DecimalString;
  /** Upper price. */
  upper: DecimalString;
  /** Color role. */
  tone: OverlayTone;
  /** Optional user color (`#rrggbb`) overriding the tone. */
  color?: string;
  /** Fill opacity 0..1. */
  fillOpacity: number;
  /** Border dash style. */
  lineStyle: OverlayLineStyle;
  /** Short label drawn inside the box. */
  label?: string;
  /** Optional dashed midline, e.g. the 50% level of a fair value gap. */
  midline?: boolean;
}

/** Point marker on a bar (structure events, signals, fills). */
export interface MarkerOverlay {
  /** Discriminator. */
  kind: 'marker';
  /** Stable id unique within the layer. */
  id: string;
  /** Time (UTC ms); the adapter snaps it to the start of its bar. */
  timeMs: TimestampMs;
  /** Placement relative to the bar. */
  position: 'above' | 'below' | 'at';
  /** Marker glyph. */
  shape: 'arrow-up' | 'arrow-down' | 'circle' | 'square';
  /** Color role. */
  tone: OverlayTone;
  /** Optional user color (`#rrggbb`) overriding the tone. */
  color?: string;
  /** Short text. */
  text?: string;
}

/** Union of all overlay models. */
export type OverlayModel = PriceLineOverlay | BoxOverlay | MarkerOverlay;

/** A named, ordered group of overlay models owned by one feature. */
export interface OverlayLayer {
  /** Unique layer id, e.g. `indicator-viz:fvg_ifvg`. */
  layerId: string;
  /**
   * Paint order of the layer's boxes and rays; higher draws on top. Markers
   * and full-width price lines are drawn by the chart library itself.
   */
  zIndex: number;
  /** Whether the layer is currently shown. */
  visible: boolean;
  /** Models to render. */
  models: readonly OverlayModel[];
  /**
   * Provenance caption such as `as of 12:05 UTC · LIVE`. Rendered by the
   * chart so users always see when backend data was computed.
   */
  caption?: string;
}
