/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Drawing tool plugin interface, registry and paint helpers.
 *
 * Tools paint and hit-test in CSS pixels relative to the plot (see
 * `surface.ts`), converting their time/price anchors through the frame's
 * `PixelTransform`. Label prices are formatted from exact decimals.
 */

import {
  type BarCalendar,
  Decimal,
  type DrawingAnchor,
  type DrawingDocument,
  type DrawingStyle,
  type Timeframe,
} from '@joseph/contracts';
import {handleAt, type Point, type Rect} from './geometry';
import type {DrawingSeriesRequest, DrawingSeriesState} from './series';
import type {PixelTransform} from './surface';

/**
 * Toolbar groups. `studies` holds engine-backed tools (anchored VWAP,
 * regression channel) whose values come from the market engine.
 */
export type ToolGroup =
  | 'lines'
  | 'shapes'
  | 'fibonacci'
  | 'gann'
  | 'patterns'
  | 'trading'
  | 'measure'
  | 'studies'
  | 'annotation';

/** Static tool description. */
export interface ToolManifest {
  /** Tool id stored in `DrawingDocument.tool_id`. */
  id: string;
  /** Tool document version (`tool_version`). */
  version: number;
  /** Toolbar label (sentence case). */
  displayName: string;
  /** Toolbar group. */
  group: ToolGroup;
  /** Icon name understood by the host (lucide icon id). */
  icon: string;
  /**
   * Anchors needed to complete a drawing. For `multi-click` and `freehand`
   * tools this is the minimum.
   */
  anchorCount: number;
  /** Optional hotkey hint, e.g. `alt+t`. */
  hotkey?: string;
  /**
   * How the drawing is created: fixed anchors (default), one anchor per
   * click finished by a double-click or Escape (`multi-click`, e.g. a
   * polyline), or a press-drag-release stroke (`freehand`, e.g. a brush).
   */
  creation?: 'anchors' | 'multi-click' | 'freehand';
  /** Anchor cap for multi-click and freehand tools (schema maximum 64). */
  maxAnchors?: number;
}

/** Schema maximum of anchors per drawing. */
export const MAX_ANCHORS = 64;

/** Colors resolved from the theme for painting. */
export interface ToolPalette {
  /**
   * Color of user-authored marks (the theme's magenta `--course` token),
   * expected as `#rrggbb`: tools append a two-digit hex alpha for fills.
   */
  user: string;
  /** Neutral text color. */
  text: string;
  /** Profit / long color, as `#rrggbb` (alpha may be appended). */
  bid: string;
  /** Loss / short color, as `#rrggbb` (alpha may be appended). */
  ask: string;
  /** Plot background (for label plates). */
  background: string;
  /** CSS font family for labels. */
  fontFamily: string;
  /** Warning accent for inline status messages (additive, optional). */
  warning?: string;
}

/** Render state for one drawing. */
export interface RenderState {
  /** Selected by the user. */
  selected: boolean;
  /** Being created (preview). */
  preview: boolean;
  /** Theme palette. */
  palette: ToolPalette;
  /** Tick size as a decimal string for label precision, or null. */
  tickSize: string | null;
  /** Bar duration in milliseconds (for measuring tools). */
  durationMs: number;
  /** Chart timeframe (with `calendar`, for counting session bars). */
  timeframe?: Timeframe;
  /**
   * Session calendar of the instrument: measuring tools count bars with it
   * so time between sessions is not counted.
   */
  calendar?: BarCalendar;
  /**
   * Engine series of this drawing for tools that declare `seriesRequest`
   * (null or absent otherwise, and while no request was made).
   */
  series?: DrawingSeriesState | null;
}

/** Boolean tool input (a checkbox in the style bar). */
export interface ToolBooleanInput {
  /** Key in `DrawingDocument.props`. */
  key: string;
  /** Sentence-case label. */
  label: string;
  /** Kind discriminator. */
  kind: 'boolean';
  /** Value when the prop is missing. */
  defaultValue: boolean;
}

/** Choice tool input (a select in the style bar). */
export interface ToolChoiceInput {
  /** Key in `DrawingDocument.props`. */
  key: string;
  /** Sentence-case label. */
  label: string;
  /** Kind discriminator. */
  kind: 'choice';
  /** Value when the prop is missing or not one of the choices. */
  defaultValue: string;
  /** Allowed values with their labels. */
  choices: ReadonlyArray<{value: string; label: string}>;
}

/**
 * Tool-specific input stored in `props` and edited generically by the
 * host's style editor (for example the anchored VWAP band multipliers).
 */
export type ToolInput = ToolBooleanInput | ToolChoiceInput;

/** Context passed to `describe` and `dataRows`. */
export interface DescribeContext {
  /** Engine series of the drawing, when the tool uses one. */
  series: DrawingSeriesState | null;
  /** Tick size of the instrument as a decimal string, or null if unknown. */
  tickSize: string | null;
}

/** One labelled value shown by the host's data window. */
export interface DrawingDataRow {
  /** Row label. */
  label: string;
  /** Formatted value. */
  value: string;
}

/** Hit test result. */
export interface HitResult {
  /** Whether the body or a handle was hit. */
  part: 'body' | 'handle';
  /** Index of the anchor handle when `part` is `handle`. */
  handleIndex?: number;
}

/** A drawing tool plugin. Tools must not import each other. */
export interface ToolPlugin {
  /** Static description: id, version, toolbar entry and anchor count. */
  readonly manifest: ToolManifest;
  /**
   * Returns the default style for new drawings.
   * @param palette Theme palette.
   * @return Style.
   */
  defaultStyle(palette: ToolPalette): DrawingStyle;
  /**
   * Returns the default tool props for new drawings.
   * @return Props stored in `DrawingDocument.props`.
   */
  defaultProps?(): Record<string, unknown>;
  /**
   * Paints a drawing; called on every frame.
   * @param ctx 2D context in CSS-pixel space.
   * @param doc Drawing to paint.
   * @param transform Transforms of the frame being painted.
   * @param state Selection, preview flag, palette and series.
   */
  render(
    ctx: CanvasRenderingContext2D,
    doc: DrawingDocument,
    transform: PixelTransform,
    state: RenderState
  ): void;
  /**
   * Tests whether a point hits the drawing.
   * @param doc Drawing to test.
   * @param transform Transforms of the current frame.
   * @param point Pointer position in CSS pixels.
   * @param tolerance Hit tolerance in CSS pixels.
   * @param series Engine series of the drawing (engine-backed tools only).
   * @return The part hit (handles take precedence), or null for a miss.
   */
  hitTest(
    doc: DrawingDocument,
    transform: PixelTransform,
    point: Point,
    tolerance: number,
    series?: DrawingSeriesState | null
  ): HitResult | null;
  /**
   * Tool-specific inputs kept in `props` and edited by the host's style
   * editor. Changing one is an undoable command like any style change.
   */
  readonly inputs?: readonly ToolInput[];
  /**
   * Returns the engine series this drawing displays (engine-backed tools
   * only). Must be a pure function of the anchors, props and timeframe; the
   * host fetches it and re-fetches when the request changes or a bar closes.
   * @param doc Drawing whose anchors and props define the request.
   * @param env Chart timeframe and the instrument's session calendar
   *     (bar boundaries; UTC bars when absent).
   * @return Request, or null when the drawing needs no series.
   */
  seriesRequest?(
    doc: DrawingDocument,
    env: {timeframe: Timeframe; calendar?: BarCalendar}
  ): DrawingSeriesRequest | null;
  /**
   * Returns the values the host's data window shows for this drawing at a
   * bar.
   * @param doc Drawing to describe.
   * @param barStartMs Start of the bar under the crosshair, epoch ms UTC.
   * @param context Series and tick size.
   * @return Rows (empty when nothing applies).
   */
  dataRows?(
    doc: DrawingDocument,
    barStartMs: number,
    context: DescribeContext
  ): DrawingDataRow[];
  /**
   * Expands anchors placed by the user into the tool's full anchor set
   * (e.g. one click for a long position yields entry, target and stop).
   * @param anchors Anchors placed by the user.
   * @param env Bar duration in milliseconds, tick size and (optionally) the
   *     timeframe and session calendar for widths measured in bars.
   * @return Complete anchors.
   */
  expandAnchors?(
    anchors: DrawingAnchor[],
    env: {
      durationMs: number;
      tickSize: string | null;
      timeframe?: Timeframe;
      calendar?: BarCalendar;
    }
  ): DrawingAnchor[];
  /**
   * Upgrades documents written by older tool versions.
   * @param doc Document with an older `tool_version`.
   * @return Upgraded document.
   */
  migrate?(doc: DrawingDocument): DrawingDocument;
  /**
   * Returns an accessible one-line description (shown in the object list).
   * @param doc Drawing to describe.
   * @param context Engine series and tick size, when the host has them.
   * @return Description.
   */
  describe?(doc: DrawingDocument, context?: DescribeContext): string;
}

/**
 * Reads a tool input from a drawing's props, falling back to the default
 * when the prop is missing or invalid.
 * @param doc Drawing (only `props` is read).
 * @param input Input declaration.
 * @return Current value: a boolean for boolean inputs, one of the choice
 *     values for choice inputs.
 */
export function readInput(
  doc: Pick<DrawingDocument, 'props'>,
  input: ToolInput
): string | boolean {
  // The generated contract types `props` as `{}`; it is a JSON object whose
  // values are checked below.
  const props = doc.props as Readonly<Record<string, unknown>> | undefined;
  const value = props?.[input.key];
  if (input.kind === 'boolean') {
    return typeof value === 'boolean' ? value : input.defaultValue;
  }
  return typeof value === 'string' &&
    input.choices.some(choice => choice.value === value)
    ? value
    : input.defaultValue;
}

/**
 * Returns the default props for a list of inputs.
 * @param inputs Input declarations.
 * @return Props with every input at its default.
 */
export function inputDefaults(
  inputs: readonly ToolInput[]
): Record<string, string | boolean> {
  return Object.fromEntries(
    inputs.map(input => [input.key, input.defaultValue])
  );
}

/** Registry of available tools. Removing a tool never touches documents. */
export class ToolRegistry {
  private readonly tools = new Map<string, ToolPlugin>();
  private readonly listeners = new Set<() => void>();

  /**
   * Registers a tool.
   * @param tool Tool plugin.
   * @return Disposer that unregisters it (a no-op once another tool took
   *     the id).
   * @throws `Error` when a tool with the same id is already registered.
   */
  register(tool: ToolPlugin): () => void {
    if (this.tools.has(tool.manifest.id)) {
      throw new Error(`Duplicate tool id: ${tool.manifest.id}`);
    }
    this.tools.set(tool.manifest.id, tool);
    this.notify();
    return () => {
      if (this.tools.get(tool.manifest.id) === tool) {
        this.tools.delete(tool.manifest.id);
        this.notify();
      }
    };
  }

  /**
   * Looks up a tool.
   * @param id Tool id.
   * @return Tool, or undefined when unknown or removed.
   */
  get(id: string): ToolPlugin | undefined {
    return this.tools.get(id);
  }

  /**
   * Lists tools in registration order.
   * @return Tools.
   */
  list(): ToolPlugin[] {
    return [...this.tools.values()];
  }

  /**
   * Subscribes to registry changes.
   * @param listener Called after each registration or removal.
   * @return Disposer that unsubscribes.
   */
  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Notifies listeners. */
  private notify(): void {
    for (const listener of [...this.listeners]) {
      listener();
    }
  }
}

/** Canvas dash patterns per line style, in CSS pixels (dash, gap). */
const DASHES: Readonly<Record<DrawingStyle['line_style'], number[]>> = {
  solid: [],
  dashed: [6, 4],
  dotted: [2, 3],
};

/**
 * Applies a drawing's stroke style to the context.
 * @param ctx Context to configure.
 * @param style Drawing style.
 * @param state Render state: selected drawings stroke 0.5 px wider,
 *     previews are dashed and lighter.
 */
export function applyStroke(
  ctx: CanvasRenderingContext2D,
  style: DrawingStyle,
  state: RenderState
): void {
  ctx.strokeStyle = style.color;
  ctx.lineWidth = style.line_width + (state.selected ? 0.5 : 0);
  ctx.setLineDash(state.preview ? [4, 4] : DASHES[style.line_style]);
  ctx.globalAlpha = state.preview ? 0.8 : 1;
}

/**
 * Draws 8 px square anchor handles, only for a selected or preview drawing.
 * @param ctx Context to paint on.
 * @param points Handle centres in CSS pixels.
 * @param state Render state.
 */
export function drawHandles(
  ctx: CanvasRenderingContext2D,
  points: readonly Point[],
  state: RenderState
): void {
  if (!state.selected && !state.preview) return;
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  for (const point of points) {
    ctx.fillStyle = state.palette.background;
    ctx.strokeStyle = state.palette.user;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.rect(point.x - 4, point.y - 4, 8, 8);
    ctx.fill();
    ctx.stroke();
  }
}

/**
 * Draws an 11 px text label on a background plate.
 * @param ctx Context to paint on.
 * @param text Label text.
 * @param x Left x of the text in CSS pixels.
 * @param y Vertical centre of the text in CSS pixels.
 * @param color Text color.
 * @param state Render state (palette background and font).
 */
export function drawLabel(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  color: string,
  state: RenderState
): void {
  ctx.save();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  ctx.font = `11px ${state.palette.fontFamily}`;
  const width = ctx.measureText(text).width;
  ctx.fillStyle = state.palette.background;
  ctx.fillRect(x - 3, y - 8, width + 6, 16);
  ctx.fillStyle = color;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, x, y);
  ctx.restore();
}

/** Approximate width of one 11px label character, for hit-testing. */
export const LABEL_CHAR_WIDTH_PX = 6;

/**
 * Returns the plate of an inline status message, kept inside the plot
 * horizontally.
 * @param textWidth Text width in CSS pixels (measured, or estimated with
 *     `LABEL_CHAR_WIDTH_PX` when no context is at hand).
 * @param x Preferred left x of the text in CSS pixels.
 * @param y Vertical centre in CSS pixels.
 * @param plotWidth Plot width in CSS pixels.
 * @return Plate rectangle, 20 px tall (the text starts 5 px inside it).
 */
export function noticeBounds(
  textWidth: number,
  x: number,
  y: number,
  plotWidth: number
): Rect {
  // Keeps the text at least 4 px from the left edge and the plate's right
  // side 7 px inside the plot (12 px minus the 5 px padding).
  const left = Math.max(4, Math.min(x, plotWidth - textWidth - 12));
  return {x: left - 5, y: y - 10, width: textWidth + 10, height: 20};
}

/**
 * Draws an inline status message (for example "Anchor is more than 2000
 * bars back") on a bordered plate in the warning accent. Tools hit-test
 * the same plate (`noticeBounds`) so a drawing whose anchor is off-screen
 * can still be selected.
 * @param ctx Context to paint on.
 * @param text Message.
 * @param x Preferred left x of the text in CSS pixels.
 * @param y Vertical centre of the text in CSS pixels.
 * @param state Render state (palette and font).
 * @param plotWidth Plot width in CSS pixels (the plate is shifted left to
 *     fit).
 */
export function drawNotice(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  state: RenderState,
  plotWidth: number
): void {
  ctx.save();
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
  ctx.font = `11px ${state.palette.fontFamily}`;
  const plate = noticeBounds(ctx.measureText(text).width, x, y, plotWidth);
  const color = state.palette.warning ?? state.palette.text;
  ctx.fillStyle = state.palette.background;
  ctx.fillRect(plate.x, plate.y, plate.width, plate.height);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.strokeRect(plate.x, plate.y, plate.width, plate.height);
  ctx.fillStyle = color;
  ctx.textBaseline = 'middle';
  ctx.fillText(text, plate.x + 5, y);
  ctx.restore();
}

/**
 * Formats a price for labels using the tick's precision.
 * @param price Price as a decimal string or a chart number.
 * @param tickSize Tick size as a decimal string; null shows 2 decimals.
 * @return Text with comma thousands separators, at most 8 fractional digits
 *     (rounded half-even) and a Unicode minus sign for negatives.
 * @throws `RangeError` when `price` or `tickSize` is not a valid decimal
 *     (or `price` is a non-finite number).
 */
export function formatPrice(
  price: string | number,
  tickSize: string | null
): string {
  const value =
    typeof price === 'number'
      ? Decimal.fromNumber(price, 10)
      : Decimal.parse(price);
  const digits = tickSize ? Decimal.parse(tickSize).normalize().scale : 2;
  const fixed = value.toFixed(Math.min(digits, 8));
  const [whole = '0', fraction] = fixed.split('.');
  const negative = whole.startsWith('-');
  const grouped = (negative ? whole.slice(1) : whole).replace(
    /\B(?=(\d{3})+(?!\d))/g,
    ','
  );
  return `${negative ? '\u2212' : ''}${grouped}${fraction ? `.${fraction}` : ''}`;
}

/**
 * Rounds a price to the tick (exact decimal arithmetic).
 * @param price Price to round.
 * @param tickSize Tick size as a decimal string; null leaves it unrounded.
 * @return Decimal string.
 * @throws `RangeError` when `tickSize` is not a positive decimal string.
 */
export function roundToTick(price: Decimal, tickSize: string | null): string {
  return tickSize
    ? price.roundToStep(Decimal.parse(tickSize)).toString()
    : price.toString();
}

/**
 * Hit-tests anchor handles.
 * @param handles Handle centres in CSS pixels, in anchor order.
 * @param point Pointer position in CSS pixels.
 * @param tolerance Hit radius in CSS pixels.
 * @return Hit of the nearest handle within the radius, or null.
 */
export function hitHandles(
  handles: readonly Point[],
  point: Point,
  tolerance: number
): HitResult | null {
  const index = handleAt(point, handles, tolerance);
  return index >= 0 ? {part: 'handle', handleIndex: index} : null;
}

/**
 * Strokes an open polyline with the context's current stroke style.
 * @param ctx Context to paint on.
 * @param points Vertices in CSS pixels.
 */
export function strokePath(
  ctx: CanvasRenderingContext2D,
  points: readonly Point[]
): void {
  ctx.beginPath();
  points.forEach((point, i) =>
    i === 0
      ? void ctx.moveTo(point.x, point.y)
      : void ctx.lineTo(point.x, point.y)
  );
  ctx.stroke();
}
