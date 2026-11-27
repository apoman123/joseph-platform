/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Renderer-neutral bridge to the 86 drawing tools used by TradeX.
 * Only the library's core scene, placement and hit-test APIs are used. Joseph
 * retains its controller, canonical documents, persistence, permissions and
 * command history; no second chart, event controller or data feed is created.
 */

import {
  Decimal,
  type DrawingAnchor,
  type DrawingDocument,
} from '@joseph/contracts';
import {
  legRatio,
  type PixelTransform,
  type SnapBar,
  type ToolGroup,
  type ToolPlugin,
  type ToolGeometryPatch,
} from '@joseph/drawing-core';
import {
  OVERLAY_SPECS,
  buildNewDrawing,
  finishPlacement,
  applyDrag,
  defaultStyleFor,
  drawScene,
  hitTestKind,
  parseDrawings,
  sceneOf,
  setImageReader,
  type Coords,
  type Drawing,
  type DrawingKind,
} from 'lightweight-charts-drawing';

/** Namespace prevents reinterpreting an existing Joseph drawing's geometry. */
export const REFERENCE_PREFIX = 'reference.';

/**
 * Decodes an embedded local raster without fetching files or remote URLs.
 * @param name Data URI for a PNG, JPEG or WebP, bounded to the 2 MB image limit.
 * @return Raster blob for the library image cache.
 * @throws Error for external URLs, malformed base64 or oversized raster input.
 */
function referenceImage(name: string): Promise<Blob> {
  const match =
    /^data:(image\/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$/.exec(name);
  if (!match || name.length > 2_800_000) {
    throw new Error('Only embedded local raster images are allowed');
  }
  const bytes = Uint8Array.from(atob(match[2] ?? ''), letter =>
    letter.charCodeAt(0)
  );
  if (bytes.length > 2_000_000) throw new Error('Drawing image exceeds 2 MB');
  return Promise.resolve(new Blob([bytes], {type: match[1]}));
}

setImageReader(referenceImage);

/**
 * Validates canonical geometry and the fixed upstream tool kind.
 * @param doc Canonical drawing from storage, an editor or automation.
 * @return True only for finite, parseable geometry of a registered tool.
 */
export function isReferenceDocument(doc: DrawingDocument): boolean {
  const kind = doc.tool_id.slice(REFERENCE_PREFIX.length);
  if (
    !doc.tool_id.startsWith(REFERENCE_PREFIX) ||
    !Object.hasOwn(OVERLAY_SPECS, kind)
  ) {
    return false;
  }
  return referenceDrawing(kind as DrawingKind, doc) !== null;
}

/**
 * Assigns the upstream catalog to Joseph's existing toolbar groups.
 * @param kind Stable upstream kind.
 * @return Existing, accessible toolbar group.
 */
function groupFor(kind: string): ToolGroup {
  if (kind === 'polyline' || kind === 'path') return 'shapes';
  if (kind === 'brush' || kind === 'highlighter') return 'annotation';
  if (/fib|pitchfan/.test(kind)) return 'fibonacci';
  if (/gann/.test(kind)) return 'gann';
  if (/elliott|pattern|three-drives|head-and/.test(kind)) return 'patterns';
  if (/position/.test(kind)) return 'trading';
  if (/range/.test(kind) && !kind.includes('volume')) return 'measure';
  if (/vwap|regression|volume-profile/.test(kind)) return 'studies';
  if (
    /text|note|pin$|comment|signpost|callout|font-icon|table|image|label|flag/.test(
      kind
    )
  ) {
    return 'annotation';
  }
  if (/line|ray|pitchfork|channel|flat-top|arrow/.test(kind)) return 'lines';
  return 'shapes';
}

/**
 * Writes a display coordinate as a plain canonical decimal price.
 * @param value Finite display price; exponent notation is expanded to 20 digits.
 * @return Plain decimal text accepted by the drawing contract.
 * @throws RangeError when the display price is outside Decimal's numeric bounds.
 */
function pointPrice(value: number): string {
  const text = String(value);
  return /e/i.test(text) ? Decimal.fromNumber(value, 20).toString() : text;
}

/**
 * Chooses a semantic lucide icon for each drawing family.
 * @param kind Fixed upstream drawing kind.
 * @param group Existing host toolbar group used as the final fallback.
 * @return Icon name supported by the terminal's shared icon registry.
 */
function iconFor(kind: string, group: ToolGroup): string {
  if (kind.startsWith('horizontal')) return 'minus';
  if (kind === 'vertical-line') return 'separator-vertical';
  if (kind === 'cross-line') return 'crosshair';
  if (kind === 'image') return 'image';
  if (kind === 'table') return 'grid-3x3';
  if (kind === 'font-icon' || kind === 'flag-mark') return 'star';
  if (/brush|highlighter|path/.test(kind)) return 'brush';
  if (kind.includes('rectangle')) {
    return kind.includes('rotated') ? 'vector-square' : 'square';
  }
  if (/ellipse/.test(kind)) return 'ellipse';
  if (/circle|arc|spiral|cycle/.test(kind)) return 'circle';
  if (/triangle|wedge/.test(kind)) return 'triangle';
  if (/pitchfork|pitchfan|fan/.test(kind)) return 'git-fork';
  if (/volume-profile/.test(kind)) return 'align-justify';
  if (/vwap/.test(kind)) return 'anchor';
  if (/regression/.test(kind)) return 'chart';
  if (/curve|sine/.test(kind)) return 'spline';
  if (/polyline/.test(kind)) return 'route';
  if (/arrow.*down/.test(kind)) return 'arrow-down-right';
  if (/arrow/.test(kind)) return 'arrow-up-right';
  if (/ray/.test(kind)) return 'arrow-right';
  if (group === 'gann') return 'grid-3x3';
  if (group === 'fibonacci') {
    return kind.includes('time') ? 'columns-2' : 'list';
  }
  if (group === 'patterns') return 'chart-network';
  if (group === 'trading') return 'target';
  if (group === 'measure') return 'ruler';
  if (group === 'annotation') return 'type';
  if (group === 'shapes') return 'shapes';
  return 'trending-up';
}

/**
 * Converts canonical anchors to the library's seconds-based data points.
 * @param anchors UTC millisecond anchors with exact decimal price strings.
 * @return Independent numeric display points; financial contracts stay exact.
 */
function pointsFor(anchors: readonly DrawingAnchor[]) {
  return anchors.map(anchor => ({
    time: (anchor.time_ms / 1000) as Drawing['points'][number]['time'],
    price: Number(anchor.price),
  }));
}

/**
 * Validates and reconstructs a reference scene from a canonical document.
 * @param kind Upstream tool kind, already selected from the fixed catalog.
 * @param doc Joseph drawing document, including optional reference style/data.
 * @return Valid native scene data, or null for incomplete/invalid documents.
 */
export function referenceDrawing(
  kind: DrawingKind,
  doc: DrawingDocument
): Drawing | null {
  if (
    doc.anchors.some(
      anchor =>
        !Number.isFinite(anchor.time_ms) ||
        Math.abs(anchor.time_ms) > 8_640_000_000_000_000 ||
        !Number.isFinite(Number(anchor.price))
    )
  ) {
    return null;
  }
  const props = (doc.props ?? {}) as Record<string, unknown>;
  const extra = props.reference_data;
  const savedStyle = props.reference_style;
  const candidate = {
    ...(extra && typeof extra === 'object' ? extra : {}),
    kind,
    id: doc.drawing_id,
    points: pointsFor(doc.anchors),
    text: doc.text ?? '',
    locked: doc.locked,
    hidden: !doc.visible,
    style: {
      ...defaultStyleFor(kind),
      ...(savedStyle && typeof savedStyle === 'object' ? savedStyle : {}),
      color: doc.style.color,
      width: doc.style.line_width,
      lineStyle: doc.style.line_style,
      ...(doc.style.fill_color ? {fillColor: doc.style.fill_color} : {}),
      ...(doc.style.font_size ? {fontSize: doc.style.font_size} : {}),
      ...(doc.style.extend_left !== undefined
        ? {extendLeft: doc.style.extend_left}
        : {}),
      ...(doc.style.extend_right !== undefined
        ? {extendRight: doc.style.extend_right}
        : {}),
    },
  };
  return parseDrawings([candidate])[0] ?? null;
}

/**
 * Converts a library time to milliseconds without importing chart-library APIs.
 * @param time UTC seconds, ISO business date, or a business-day object.
 * @return UTC epoch milliseconds.
 */
function milliseconds(time: Parameters<Coords['timeToX']>[0]): number {
  if (typeof time === 'number') return time * 1000;
  if (typeof time === 'string') return Date.parse(`${time}T00:00:00Z`);
  return Date.UTC(time.year, time.month - 1, time.day);
}

/**
 * Supplies data-space coordinates to the renderer-neutral drawing core.
 * @param transform Current Joseph plot coordinates; no chart-library instance.
 * @param bars Actual visible/replay-bounded bars, sorted by time.
 * @param durationMs Nominal bar duration for off-data extrapolation.
 * @param tickSize Optional positive price increment.
 * @return Coordinate port for scenes and data-derived display annotations.
 */
function coordinates(
  transform: PixelTransform,
  bars: readonly SnapBar[],
  durationMs: number,
  tickSize: string | null
): Coords {
  const nativeBars = bars.map(bar => ({
    ...bar,
    time: (bar.timeMs / 1000) as Drawing['points'][number]['time'],
    volume: bar.volume ?? 0,
  }));
  return {
    priceToY: price => transform.priceToY(price),
    yToPrice: y => transform.yToPrice(y),
    timeToX: time => transform.timeToX(milliseconds(time)),
    xToTime: x => {
      const time = transform.xToTime(x);
      return time === null
        ? null
        : ((time / 1000) as Drawing['points'][number]['time']);
    },
    isLog: () => transform.isLog?.() ?? false,
    barAt: time => {
      const target = milliseconds(time);
      let left = 0;
      let right = bars.length - 1;
      while (left <= right) {
        const middle = (left + right) >>> 1;
        if ((bars[middle]?.timeMs ?? Infinity) <= target) left = middle + 1;
        else right = middle - 1;
      }
      return nativeBars[right] ?? null;
    },
    timeToBarIndex: time => {
      const x = transform.timeToX(milliseconds(time));
      const first = bars[0];
      if (x === null || !first) return null;
      const firstX = transform.timeToX(first.timeMs);
      const nextX = transform.timeToX(
        bars[1]?.timeMs ?? first.timeMs + durationMs
      );
      return firstX === null || nextX === null || nextX === firstX
        ? null
        : (x - firstX) / (nextX - firstX);
    },
    barIndexToTime: index => {
      const first = bars[0];
      if (!first) return null;
      const firstX = transform.timeToX(first.timeMs);
      const nextX = transform.timeToX(
        bars[1]?.timeMs ?? first.timeMs + durationMs
      );
      if (firstX === null || nextX === null) return null;
      const time = transform.xToTime(firstX + index * (nextX - firstX));
      return time === null
        ? null
        : ((time / 1000) as Drawing['points'][number]['time']);
    },
    bars: () => nativeBars,
    visibleTimeRange: () => {
      const from = transform.xToTime(0);
      const to = transform.xToTime(transform.width);
      return from === null || to === null
        ? null
        : {from: from / 1000, to: to / 1000};
    },
    indicatorValuesAt: () => [],
    pipSize: () => (Number(tickSize) > 0 ? Number(tickSize) : 0.01),
    addPriceLine: () => {
      throw new Error(
        'Axis objects are owned by ChartPort, not drawing scenes'
      );
    },
    removePriceLine: () => {},
    zoomToScreenRect: () => {},
    formatTime: time => new Date(milliseconds(time)).toISOString(),
    timeAxis: () => ({
      height: 0,
      width: transform.width,
      fontSize: 12,
      fontFamily: 'system-ui',
    }),
    timeInfo: () => ({timeZone: 'UTC', intraday: durationMs < 86_400_000}),
  };
}

/**
 * Projects native points without collapsing missing coordinates to zero.
 * @param drawing Valid native drawing.
 * @param transform Current plot coordinate port.
 * @return All projected points, or null while the chart has no coordinates.
 */
function pixels(drawing: Drawing, transform: PixelTransform) {
  const points = drawing.points.map(point => ({
    x: transform.timeToX(milliseconds(point.time)),
    y: transform.priceToY(point.price),
  }));
  if (points.some(point => point.x === null || point.y === null)) return null;
  return points.map(point => ({x: point.x ?? 0, y: point.y ?? 0}));
}

/**
 * Converts completed upstream geometry back to Joseph's canonical document.
 * @param drawing Valid completed native drawing including placement settings.
 * @return Canonical editable fields; native identities never replace Joseph ids.
 */
function geometryPatch(drawing: Omit<Drawing, 'id'>): ToolGeometryPatch {
  const {points, style, kind, ...data} = drawing;
  const resolved = style ?? defaultStyleFor(kind);
  return {
    anchors: points.map(point => ({
      time_ms: milliseconds(point.time),
      price: pointPrice(point.price),
    })),
    props: {reference_style: resolved, reference_data: data},
    style: {
      color: resolved.color,
      line_width: Math.max(1, Math.min(50, Math.round(resolved.width))),
      line_style: resolved.lineStyle,
    },
    text: drawing.text ?? null,
  };
}

/**
 * Builds a removable Joseph plugin for one upstream drawing kind.
 * @param kind Fixed upstream kind; never evaluated as code.
 * @return Plugin using upstream geometry and Joseph's interaction/history layer.
 */
function referenceTool(kind: DrawingKind): ToolPlugin {
  const spec = OVERLAY_SPECS[kind];
  const defaults = defaultStyleFor(kind);
  const group = groupFor(kind);
  return {
    manifest: {
      id: REFERENCE_PREFIX + kind,
      version: 1,
      displayName: kind
        .replaceAll('-', ' ')
        .replace(/^./, letter => letter.toUpperCase())
        .replace('Xabcd', 'XABCD')
        .replace('Abcd', 'ABCD')
        .replace('vwap', 'VWAP'),
      group,
      icon: iconFor(kind, group),
      anchorCount: spec.pointCount,
      ...(spec.variableLength
        ? {
            creation: spec.freehand
              ? ('freehand' as const)
              : ('multi-click' as const),
            maxAnchors: 64,
          }
        : {}),
    },
    defaultStyle: () => ({
      color: defaults.color,
      line_width: Math.max(1, Math.min(50, Math.round(defaults.width))),
      line_style: defaults.lineStyle,
    }),
    defaultProps: () => ({reference_style: defaultStyleFor(kind)}),
    expandAnchors: anchors => {
      const expanded = buildNewDrawing(kind, pointsFor(anchors));
      return expanded
        ? expanded.points.map(point => ({
            time_ms: milliseconds(point.time),
            price: pointPrice(point.price),
          }))
        : anchors;
    },
    place(anchors, context) {
      const transform = context.transform;
      if (!transform) return null;
      const coords = coordinates(
        transform,
        context.bars,
        context.durationMs,
        context.tickSize
      );
      const result = finishPlacement(
        kind,
        pointsFor(anchors),
        coords,
        {w: transform.width, h: transform.height},
        {glyph: '★'}
      );
      return result
        ? geometryPatch({
            ...result,
            style: result.style ?? defaultStyleFor(kind),
          })
        : null;
    },
    edit(doc, drag, context) {
      const drawing = referenceDrawing(kind, doc);
      const transform = context.transform;
      if (!drawing || !transform) return null;
      const screen = pixels(drawing, transform);
      if (!screen) return null;
      const result = applyDrag(
        {
          id: drawing.id,
          start: drawing,
          startCursor: drag.start,
          startScreen: screen,
          mode:
            drag.handleIndex === null
              ? {hit: 'body'}
              : {hit: 'handle', handleIndex: drag.handleIndex},
          active: true,
        },
        drag.current,
        coordinates(
          transform,
          context.bars,
          context.durationMs,
          context.tickSize
        ),
        point => point,
        drag.constrain
      );
      if (!result) return null;
      const patch = geometryPatch(result);
      return {
        ...patch,
        props: {
          ...(doc.props as Record<string, unknown>),
          ...(patch.props as Record<string, unknown>),
        },
      };
    },
    render(ctx, doc, transform, state) {
      const drawing = referenceDrawing(kind, doc);
      if (!drawing) return;
      const points = pixels(drawing, transform);
      if (!points) return;
      const coords = coordinates(
        transform,
        state.bars ?? [],
        state.durationMs,
        state.tickSize
      );
      const scene = sceneOf(drawing, points, {
        w: transform.width,
        h: transform.height,
        coords,
        selected: state.selected || state.preview,
      });
      drawScene(ctx, scene, {
        fontFamily: state.palette.fontFamily,
        anchors: {
          fillAt: () => state.palette.background,
          selected: state.selected,
        },
        onAsyncLoad: state.invalidate,
      });
    },
    hitTest(doc, transform, point, tolerance) {
      const drawing = referenceDrawing(kind, doc);
      if (!drawing) return null;
      const points = pixels(drawing, transform);
      if (!points) return null;
      const hit = hitTestKind(
        drawing,
        points,
        point,
        transform.width,
        transform.height,
        coordinates(transform, [], 60_000, null),
        true
      );
      void tolerance;
      return hit?.hit === 'handle'
        ? {part: 'handle', handleIndex: hit.handleIndex}
        : hit
          ? {part: 'body'}
          : null;
    },
    describe: doc => {
      if (kind === 'xabcd-pattern') {
        return `AB/XA ${legRatio(doc.anchors, [1, 2], [0, 1]) ?? '?'} > BC/AB ${legRatio(doc.anchors, [2, 3], [1, 2]) ?? '?'} > CD/BC ${legRatio(doc.anchors, [3, 4], [2, 3]) ?? '?'} > AD/XA ${legRatio(doc.anchors, [1, 4], [0, 1]) ?? '?'}`.replaceAll(
          ' > ',
          ', '
        );
      }
      return `${kind.replaceAll('-', ' ')} · ${doc.anchors.length} anchors`;
    },
  };
}

/** Complete upstream drawing catalog, grouped for the existing left toolbar. */
export const REFERENCE_TOOLS: readonly ToolPlugin[] = (
  Object.keys(OVERLAY_SPECS) as DrawingKind[]
)
  .map(referenceTool)
  .sort((a, b) => a.manifest.group.localeCompare(b.manifest.group));
