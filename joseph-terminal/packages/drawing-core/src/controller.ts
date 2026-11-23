/**
 * @fileoverview `DrawingController`: binds surface pointer events to tool
 * plugins, the command store and rendering.
 *
 * Creation supports press-drag-release and click-per-anchor. Editing drags
 * bodies or individual handles; a whole drag gesture is one undo entry.
 * Drawings whose tool is missing are rendered as an "unsupported drawing"
 * placeholder and are never deleted implicitly.
 */
import type {
  BarCalendar,
  DrawingAnchor,
  DrawingDocument,
  InstrumentId,
  Timeframe,
} from '@joseph/contracts';
import {Decimal} from '@joseph/contracts';
import {
  anchorFromPoint,
  anchorPoints,
  appliesToTimeframe,
  createDrawing,
  translateDrawing,
} from './document';
import {MOUSE_TOLERANCE_PX, decimate, distance, type Point} from './geometry';
import type {DrawingSeriesState} from './series';
import {magnetSnap} from './snapping';
import {createCommand, type DrawingStore} from './store';
import type {
  Disposer,
  DrawingSurface,
  PixelTransform,
  SnapBar,
  SurfacePointerEvent,
} from './surface';
import {
  MAX_ANCHORS,
  type RenderState,
  type ToolPalette,
  type ToolPlugin,
  type ToolRegistry,
} from './tool';

/** Movement (px) that turns a press into a drag. */
const DRAG_SLOP_PX = 4;
/** Minimum spacing (px) between recorded freehand points. */
const FREEHAND_STEP_PX = 3;
/** Distance (px) at which a second click finishes a multi-click drawing. */
const FINISH_CLICK_PX = 6;

/** Environment the controller reads on every gesture. */
export interface DrawingControllerOptions {
  /** Instrument shown on the chart. */
  instrumentId: () => InstrumentId;
  /** Timeframe shown on the chart. */
  timeframe: () => Timeframe;
  /** Tick size for anchor rounding. */
  tickSize: () => string | null;
  /** Bars for magnet snapping (sorted). */
  bars: () => readonly SnapBar[];
  /** Bar duration. */
  durationMs: () => number;
  /**
   * Session calendar of the instrument (bar counts and widths in session
   * bars); UTC bars when absent.
   */
  calendar?: () => BarCalendar;
  /** Whether magnet snapping is on. */
  magnet: () => boolean;
  /** Theme palette. */
  palette: () => ToolPalette;
  /** Clock. */
  now: () => number;
  /** Id generator. */
  newId: () => string;
  /** Called when the armed tool changes (null = pointer). */
  onToolChange?: (toolId: string | null) => void;
  /** Called when the selection changes. */
  onSelectionChange?: (drawingId: string | null) => void;
  /** Whether the armed tool stays armed after a drawing (default false). */
  stickyTools?: () => boolean;
  /**
   * Engine series of a drawing (engine-backed tools), usually from a
   * `DrawingSeriesSync`.
   */
  series?: (doc: DrawingDocument) => DrawingSeriesState | null;
}

/** In-progress creation. */
interface Draft {
  /** Tool id. */
  toolId: string;
  /** Anchors (last one follows the pointer). */
  anchors: DrawingAnchor[];
  /** Anchors confirmed so far. */
  placed: number;
  /** Where the press started. */
  pressPoint: Point;
  /** Creation mode of the tool. */
  mode: 'anchors' | 'multi-click' | 'freehand';
  /** Last recorded pointer position (freehand spacing). */
  lastPoint: Point;
}

/** In-progress edit drag. */
interface EditDrag {
  /** Drawing id. */
  id: string;
  /** Handle index, or null for body moves. */
  handleIndex: number | null;
  /** Anchor under the pointer at press time. */
  origin: DrawingAnchor;
  /** Document at press time. */
  original: DrawingDocument;
  /** Gesture id for merging history entries. */
  gesture: string;
  /** Whether the pointer moved beyond the slop. */
  moved: boolean;
  /** Press position. */
  pressPoint: Point;
}

/** Drawing interaction controller for one chart surface. */
export class DrawingController {
  private toolId: string | null = null;
  private selectedId: string | null = null;
  private draft: Draft | null = null;
  private edit: EditDrag | null = null;
  private gestureSeq = 0;
  private readonly disposers: Disposer[] = [];

  /**
   * Creates and attaches the controller.
   * @param surface Chart surface.
   * @param store Drawing store for the current instrument.
   * @param tools Tool registry.
   * @param options Environment accessors.
   */
  constructor(
    private readonly surface: DrawingSurface,
    private readonly store: DrawingStore,
    private readonly tools: ToolRegistry,
    private readonly options: DrawingControllerOptions
  ) {
    this.disposers.push(
      surface.on('pointerDown', event => this.onPointerDown(event)),
      surface.on('pointerMove', event => this.onPointerMove(event)),
      surface.on('pointerUp', event => this.onPointerUp(event)),
      surface.addCanvasLayer({
        id: 'drawings',
        zIndex: 100,
        draw: (ctx, transform) => this.render(ctx, transform),
      }),
      store.subscribe(() => surface.invalidate()),
      tools.subscribe(() => surface.invalidate())
    );
  }

  /**
   * Armed tool id, or null for the pointer.
   * @return Tool id or null.
   */
  get activeTool(): string | null {
    return this.toolId;
  }

  /**
   * Selected drawing id.
   * @return Drawing id or null.
   */
  get selection(): string | null {
    return this.selectedId;
  }

  /**
   * Arms a tool (or the pointer with null). Unknown ids are ignored.
   * @param toolId Tool id or null.
   */
  setTool(toolId: string | null): void {
    if (toolId !== null && !this.tools.get(toolId)) return;
    this.toolId = toolId;
    this.draft = null;
    this.surface.setInteractive(toolId === null);
    this.options.onToolChange?.(toolId);
    this.surface.invalidate();
  }

  /**
   * Selects a drawing.
   * @param id Drawing id or null.
   */
  select(id: string | null): void {
    if (this.selectedId === id) return;
    this.selectedId = id;
    this.options.onSelectionChange?.(id);
    this.surface.invalidate();
  }

  /**
   * Handles a keyboard key.
   * @param key `KeyboardEvent.key`.
   * @return True when handled.
   */
  handleKey(key: string): boolean {
    if (key === 'Escape') {
      if (this.draft) {
        // Escape finishes a polyline that already has a segment.
        if (!this.finishDraft()) {
          this.draft = null;
          this.surface.invalidate();
        }
        return true;
      }
      if (this.toolId) {
        this.setTool(null);
        return true;
      }
      this.select(null);
      return true;
    }
    if ((key === 'Delete' || key === 'Backspace') && this.selectedId) {
      const removed = this.store.remove(this.selectedId);
      if (removed) this.select(null);
      return removed;
    }
    return false;
  }

  /**
   * Finishes a multi-click drawing with the anchors placed so far.
   * @return True when a drawing was created.
   */
  finishDraft(): boolean {
    const draft = this.draft;
    const tool = draft ? this.tools.get(draft.toolId) : undefined;
    if (!draft || !tool || draft.mode !== 'multi-click') return false;
    const placed = draft.anchors.slice(0, draft.placed);
    if (placed.length < Math.max(2, tool.manifest.anchorCount)) return false;
    this.draft = null;
    this.commit(draft.toolId, placed);
    this.surface.invalidate();
    return true;
  }

  /**
   * Whether a multi-click or freehand drawing is being created.
   * @return True while drafting.
   */
  get drafting(): boolean {
    return this.draft !== null;
  }

  /**
   * Converts a pointer event to a (magnet-snapped) anchor.
   * @param event Pointer event.
   * @return Anchor or null.
   */
  private anchorFor(event: SurfacePointerEvent): DrawingAnchor | null {
    const transform = this.surface.transform();
    if (!transform) return null;
    let point: Point = {x: event.x, y: event.y};
    if (this.options.magnet() !== event.modKey) {
      point = magnetSnap(
        point,
        this.options.bars(),
        transform,
        this.options.durationMs()
      );
    }
    return anchorFromPoint(transform, point, this.options.tickSize());
  }

  /**
   * Pointer down: starts creation or an edit drag.
   * @param event Pointer event.
   */
  private onPointerDown(event: SurfacePointerEvent): void {
    if (event.button !== 0) return;
    if (this.toolId) {
      event.preventDefault();
      this.startOrAdvanceDraft(event);
      return;
    }
    const hit = this.hitTest({x: event.x, y: event.y});
    if (!hit) {
      this.select(null);
      return;
    }
    this.select(hit.doc.drawing_id);
    if (hit.doc.locked || !this.tools.get(hit.doc.tool_id)) return;
    const origin = this.anchorFor(event);
    if (!origin) return;
    event.preventDefault();
    this.gestureSeq += 1;
    this.edit = {
      id: hit.doc.drawing_id,
      handleIndex: hit.handleIndex,
      origin,
      original: hit.doc,
      gesture: `drag:${hit.doc.drawing_id}:${this.gestureSeq}`,
      moved: false,
      pressPoint: {x: event.x, y: event.y},
    };
  }

  /**
   * Starts a draft or confirms the next anchor (click-per-anchor mode).
   * @param event Pointer event.
   */
  private startOrAdvanceDraft(event: SurfacePointerEvent): void {
    const tool = this.toolId ? this.tools.get(this.toolId) : undefined;
    const anchor = this.anchorFor(event);
    if (!tool || !anchor) return;
    const mode = tool.manifest.creation ?? 'anchors';
    const point = {x: event.x, y: event.y};
    if (mode === 'freehand') {
      this.draft = {
        toolId: tool.manifest.id,
        anchors: [anchor],
        placed: 1,
        pressPoint: point,
        mode,
        lastPoint: point,
      };
      this.surface.invalidate();
      return;
    }
    if (!this.draft) {
      if (mode === 'anchors' && tool.manifest.anchorCount <= 1) {
        this.commit(tool.manifest.id, [anchor]);
        return;
      }
      this.draft = {
        toolId: tool.manifest.id,
        anchors: [anchor, {...anchor}],
        placed: 1,
        pressPoint: point,
        mode,
        lastPoint: point,
      };
    } else if (this.draft.mode === 'multi-click') {
      this.advanceMultiClick(tool, anchor, point);
    } else {
      this.draft.anchors[this.draft.anchors.length - 1] = anchor;
      this.draft.placed += 1;
      this.draft.pressPoint = {x: event.x, y: event.y};
      this.advanceOrCommit(tool.manifest.anchorCount);
    }
    this.surface.invalidate();
  }

  /**
   * Places the next polyline vertex, or finishes on a second click at the
   * same spot (double-click) or when the anchor cap is reached.
   * @param tool Tool plugin.
   * @param anchor Anchor under the pointer.
   * @param point Pointer position.
   */
  private advanceMultiClick(
    tool: ToolPlugin,
    anchor: DrawingAnchor,
    point: Point
  ): void {
    const draft = this.draft;
    if (!draft) return;
    if (distance(draft.lastPoint, point) <= FINISH_CLICK_PX) {
      this.finishDraft();
      return;
    }
    draft.anchors[draft.anchors.length - 1] = anchor;
    draft.placed += 1;
    draft.pressPoint = point;
    draft.lastPoint = point;
    const cap = Math.min(MAX_ANCHORS, tool.manifest.maxAnchors ?? MAX_ANCHORS);
    if (draft.placed >= cap) {
      this.finishDraft();
      return;
    }
    draft.anchors.push({...anchor});
  }

  /**
   * Commits the draft when complete, otherwise adds a following anchor.
   * @param anchorCount Anchors the tool needs.
   */
  private advanceOrCommit(anchorCount: number): void {
    const draft = this.draft;
    if (!draft) return;
    if (draft.placed >= anchorCount) {
      this.draft = null;
      this.commit(draft.toolId, draft.anchors.slice(0, anchorCount));
    } else {
      const last = draft.anchors[draft.anchors.length - 1];
      if (last) draft.anchors.push({...last});
    }
  }

  /**
   * Pointer move: updates previews and drags.
   * @param event Pointer event.
   */
  private onPointerMove(event: SurfacePointerEvent): void {
    if (this.draft?.mode === 'freehand') {
      const point = {x: event.x, y: event.y};
      if (distance(this.draft.lastPoint, point) < FREEHAND_STEP_PX) return;
      const anchor = this.anchorFor(event);
      if (anchor) {
        this.draft.anchors.push(anchor);
        this.draft.lastPoint = point;
        this.surface.invalidate();
      }
      return;
    }
    if (this.draft) {
      const anchor = this.anchorFor(event);
      if (anchor) {
        this.draft.anchors[this.draft.anchors.length - 1] = anchor;
        this.surface.invalidate();
      }
      return;
    }
    const edit = this.edit;
    if (!edit) return;
    if (!edit.moved && distance(edit.pressPoint, event) < DRAG_SLOP_PX) return;
    edit.moved = true;
    const anchor = this.anchorFor(event);
    if (!anchor) return;
    const now = this.options.now();
    this.store.update(
      edit.id,
      () => {
        if (edit.handleIndex === null) {
          const deltaPrice = Decimal.parse(anchor.price)
            .sub(Decimal.parse(edit.origin.price))
            .toString();
          return translateDrawing(
            edit.original,
            anchor.time_ms - edit.origin.time_ms,
            deltaPrice,
            now
          );
        }
        const anchors = edit.original.anchors.map((a, i) =>
          i === edit.handleIndex ? anchor : a
        );
        return {...edit.original, anchors, updated_at_ms: now};
      },
      edit.handleIndex === null ? 'Move drawing' : 'Resize drawing',
      edit.gesture
    );
  }

  /**
   * Pointer up: finishes drag-create or edit gestures.
   * @param event Pointer event.
   */
  private onPointerUp(event: SurfacePointerEvent): void {
    const draft = this.draft;
    if (draft?.mode === 'freehand') {
      const tool = this.tools.get(draft.toolId);
      this.draft = null;
      if (tool && draft.anchors.length >= 2) {
        const cap = Math.min(
          MAX_ANCHORS,
          tool.manifest.maxAnchors ?? MAX_ANCHORS
        );
        this.commit(draft.toolId, decimate(draft.anchors, cap));
      }
      this.surface.invalidate();
      return;
    }
    if (draft?.mode === 'multi-click') {
      // Vertices are placed by clicks; a drag places one at the release.
      if (distance(draft.pressPoint, event) >= DRAG_SLOP_PX) {
        const tool = this.tools.get(draft.toolId);
        const anchor = this.anchorFor(event);
        if (tool && anchor)
          this.advanceMultiClick(tool, anchor, {x: event.x, y: event.y});
      }
      this.surface.invalidate();
      return;
    }
    if (draft) {
      const tool = this.tools.get(draft.toolId);
      const dragged = distance(draft.pressPoint, event) >= DRAG_SLOP_PX;
      if (tool && dragged) {
        const anchor = this.anchorFor(event);
        if (anchor) draft.anchors[draft.anchors.length - 1] = anchor;
        draft.placed += 1;
        draft.pressPoint = {x: event.x, y: event.y};
        this.advanceOrCommit(tool.manifest.anchorCount);
      }
      this.surface.invalidate();
      return;
    }
    if (this.edit) {
      this.edit = null;
      this.surface.setInteractive(this.toolId === null);
    }
  }

  /**
   * Creates a drawing from anchors via a command.
   * @param toolId Tool id.
   * @param anchors Anchors.
   */
  private commit(toolId: string, anchors: DrawingAnchor[]): void {
    const tool = this.tools.get(toolId);
    if (!tool) return;
    const complete =
      tool.expandAnchors?.(anchors, {
        durationMs: this.options.durationMs(),
        tickSize: this.options.tickSize(),
        timeframe: this.options.timeframe(),
        calendar: this.options.calendar?.(),
      }) ?? anchors;
    const drawings = this.store.get().drawings;
    const zIndex = drawings.reduce((max, d) => Math.max(max, d.z_index), 0) + 1;
    const doc = createDrawing({
      toolId,
      toolVersion: tool.manifest.version,
      instrumentId: this.options.instrumentId(),
      timeframe: null,
      anchors: complete,
      style: tool.defaultStyle(this.options.palette()),
      props: tool.defaultProps?.(),
      text: toolId === 'text' ? 'Note' : null,
      zIndex,
      nowMs: this.options.now(),
      newId: this.options.newId,
    });
    this.store.execute(createCommand(doc));
    this.select(doc.drawing_id);
    if (!this.options.stickyTools?.()) this.setTool(null);
  }

  /**
   * Finds the topmost visible drawing under a point.
   * @param point Point.
   * @return Hit drawing and handle, or null.
   */
  hitTest(
    point: Point
  ): {doc: DrawingDocument; handleIndex: number | null} | null {
    const transform = this.surface.transform();
    if (!transform) return null;
    const visible = this.visibleDrawings();
    for (const doc of [...visible].reverse()) {
      const tool = this.tools.get(doc.tool_id);
      if (!tool) {
        const points = anchorPoints(transform, doc) ?? [];
        if (points.some(p => distance(p, point) <= MOUSE_TOLERANCE_PX * 2)) {
          return {doc, handleIndex: null};
        }
        continue;
      }
      const hit = tool.hitTest(
        doc,
        transform,
        point,
        MOUSE_TOLERANCE_PX,
        this.options.series?.(doc) ?? null
      );
      if (hit) {
        return {
          doc,
          handleIndex: hit.part === 'handle' ? (hit.handleIndex ?? null) : null,
        };
      }
    }
    return null;
  }

  /**
   * Drawings shown for the current instrument and timeframe.
   * @return Drawings in z order.
   */
  visibleDrawings(): DrawingDocument[] {
    const instrumentId = this.options.instrumentId();
    const timeframe = this.options.timeframe();
    return this.store
      .get()
      .drawings.filter(
        doc =>
          doc.visible &&
          doc.instrument_id === instrumentId &&
          appliesToTimeframe(doc, timeframe)
      );
  }

  /**
   * Paints drawings, placeholders and the draft.
   * @param ctx Context.
   * @param transform Transform.
   */
  private render(
    ctx: CanvasRenderingContext2D,
    transform: PixelTransform
  ): void {
    const base: Omit<RenderState, 'selected' | 'preview'> = {
      palette: this.options.palette(),
      tickSize: this.options.tickSize(),
      durationMs: this.options.durationMs(),
      timeframe: this.options.timeframe(),
      calendar: this.options.calendar?.(),
    };
    for (const doc of this.visibleDrawings()) {
      ctx.save();
      const tool = this.tools.get(doc.tool_id);
      const state: RenderState = {
        ...base,
        selected: doc.drawing_id === this.selectedId,
        preview: false,
        series: this.options.series?.(doc) ?? null,
      };
      if (tool) {
        tool.render(ctx, doc, transform, state);
      } else {
        renderUnsupported(ctx, doc, transform, state);
      }
      ctx.restore();
    }
    const draft = this.draft;
    const tool = draft ? this.tools.get(draft.toolId) : undefined;
    if (draft && tool) {
      const preview = createDrawing({
        toolId: draft.toolId,
        toolVersion: tool.manifest.version,
        instrumentId: this.options.instrumentId(),
        timeframe: null,
        anchors: draft.anchors,
        style: tool.defaultStyle(base.palette),
        props: tool.defaultProps?.(),
        zIndex: 0,
        nowMs: 0,
        newId: () => 'draft',
      });
      ctx.save();
      tool.render(ctx, preview, transform, {
        ...base,
        selected: false,
        preview: true,
      });
      ctx.restore();
    }
  }

  /** Detaches every listener and the canvas layer. */
  dispose(): void {
    for (const dispose of this.disposers.splice(0).reverse()) dispose();
    this.surface.setInteractive(true);
  }
}

/**
 * Paints a placeholder for a drawing whose tool is not installed.
 * @param ctx Context.
 * @param doc Document.
 * @param transform Transform.
 * @param state Render state.
 */
export function renderUnsupported(
  ctx: CanvasRenderingContext2D,
  doc: DrawingDocument,
  transform: PixelTransform,
  state: RenderState
): void {
  const points = anchorPoints(transform, doc);
  if (!points || points.length === 0) return;
  ctx.strokeStyle = state.palette.text;
  ctx.globalAlpha = 0.6;
  ctx.setLineDash([2, 4]);
  ctx.lineWidth = 1;
  ctx.beginPath();
  points.forEach((point, i) =>
    i === 0 ? ctx.moveTo(point.x, point.y) : ctx.lineTo(point.x, point.y)
  );
  ctx.stroke();
  ctx.setLineDash([]);
  const first = points[0];
  if (first) {
    ctx.fillStyle = state.palette.text;
    ctx.font = `11px ${state.palette.fontFamily}`;
    ctx.fillText(
      `Unsupported drawing (${doc.tool_id})`,
      first.x + 6,
      first.y - 6
    );
  }
}
