/**
 * @fileoverview Tests for multi-click and freehand creation, decimation,
 * z-order moves and undoable style/text/flag edits.
 */
import type {DrawingDocument} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {DrawingController} from './controller';
import {createDrawing} from './document';
import {
  reorderDrawing,
  restyleDrawing,
  retextDrawing,
  setDrawingFlags,
  setDrawingInputs,
  zOrderChanges,
} from './editing';
import {decimate} from './geometry';
import {DrawingStore, createCommand} from './store';
import type {
  DrawingSurface,
  PixelTransform,
  SurfaceCanvasLayer,
  SurfaceEventMap,
  SurfacePointerEvent,
} from './surface';
import {
  MAX_ANCHORS,
  ToolRegistry,
  inputDefaults,
  readInput,
  type ToolInput,
  type ToolPlugin,
} from './tool';

const TRANSFORM: PixelTransform = {
  width: 1000,
  height: 500,
  timeToX: t => (t / 60_000) * 10,
  xToTime: x => (x / 10) * 60_000,
  priceToY: p => ((200 - p) / 100) * 500,
  yToPrice: y => 200 - (y / 500) * 100,
};

/** Minimal surface firing synthetic pointer events. */
class Surface implements DrawingSurface {
  private readonly handlers = new Map<string, ((e: never) => void)[]>();

  /**
   * Subscribes to an event.
   * @param event Event.
   * @param handler Handler.
   * @return Disposer.
   */
  on<E extends keyof SurfaceEventMap>(
    event: E,
    handler: (payload: SurfaceEventMap[E]) => void
  ): () => void {
    const list = this.handlers.get(event) ?? [];
    list.push(handler as (e: never) => void);
    this.handlers.set(event, list);
    return () => undefined;
  }

  /**
   * Accepts a layer.
   * @param _layer Layer.
   * @return Disposer.
   */
  addCanvasLayer(_layer: SurfaceCanvasLayer): () => void {
    return () => undefined;
  }

  /** No-op repaint. */
  invalidate(): void {}

  /**
   * No-op interactivity.
   * @param _enabled Ignored.
   */
  setInteractive(_enabled: boolean): void {}

  /**
   * Returns the fixed transform.
   * @return Transform.
   */
  transform(): PixelTransform {
    return TRANSFORM;
  }

  /**
   * Fires a pointer event.
   * @param kind Event.
   * @param x X.
   * @param y Y.
   */
  fire(
    kind: 'pointerDown' | 'pointerMove' | 'pointerUp',
    x: number,
    y: number
  ): void {
    const event: SurfacePointerEvent = {
      x,
      y,
      timeMs: TRANSFORM.xToTime(x),
      price: TRANSFORM.yToPrice(y),
      button: 0,
      shiftKey: false,
      modKey: false,
      preventDefault: () => undefined,
    };
    for (const handler of this.handlers.get(kind) ?? []) {
      (handler as (e: SurfacePointerEvent) => void)(event);
    }
  }

  /**
   * Clicks (down + up) at a point.
   * @param x X.
   * @param y Y.
   */
  click(x: number, y: number): void {
    this.fire('pointerDown', x, y);
    this.fire('pointerUp', x, y);
  }
}

/**
 * Builds a tool with a creation mode.
 * @param id Tool id.
 * @param creation Creation mode.
 * @param maxAnchors Anchor cap.
 * @return Tool.
 */
function tool(
  id: string,
  creation: 'multi-click' | 'freehand',
  maxAnchors?: number
): ToolPlugin {
  return {
    manifest: {
      id,
      version: 1,
      displayName: id,
      group: 'lines',
      icon: 'minus',
      anchorCount: 2,
      creation,
      ...(maxAnchors ? {maxAnchors} : {}),
    },
    defaultStyle: palette => ({
      color: palette.user,
      line_width: 2,
      line_style: 'solid',
    }),
    render: () => undefined,
    hitTest: () => null,
  };
}

/**
 * Creates a controller with the polyline and brush test tools.
 * @return Controller, surface and store.
 */
function setup() {
  const tools = new ToolRegistry();
  tools.register(tool('poly', 'multi-click', 4));
  tools.register(tool('brush', 'freehand', 10));
  const store = new DrawingStore();
  const surface = new Surface();
  let seq = 0;
  const controller = new DrawingController(surface, store, tools, {
    instrumentId: () => 'mock:perp:BTCUSD',
    timeframe: () => '1m',
    tickSize: () => '0.1',
    bars: () => [],
    durationMs: () => 60_000,
    magnet: () => false,
    palette: () => ({
      user: '#e66fd2',
      text: '#ffffff',
      bid: '#00ff00',
      ask: '#ff0000',
      background: '#000000',
      fontFamily: 'Archivo',
    }),
    now: () => 5,
    newId: () => `d${++seq}`,
  });
  return {controller, surface, store};
}

describe('multi-click creation (polyline)', () => {
  it('adds a vertex per click and finishes on a double-click', () => {
    const {controller, surface, store} = setup();
    controller.setTool('poly');
    surface.click(100, 100);
    surface.click(200, 150);
    expect(controller.drafting).toBe(true);
    surface.click(300, 120);
    surface.click(302, 121);
    expect(controller.drafting).toBe(false);
    const [doc] = store.get().drawings;
    expect(doc?.tool_id).toBe('poly');
    expect(doc?.anchors.map(a => a.time_ms)).toEqual([
      600_000, 1_200_000, 1_800_000,
    ]);
    expect(controller.activeTool).toBeNull();
  });

  it('finishes with Escape after a segment and cancels before one', () => {
    const {controller, surface, store} = setup();
    controller.setTool('poly');
    surface.click(100, 100);
    expect(controller.handleKey('Escape')).toBe(true);
    expect(store.get().drawings).toHaveLength(0);
    controller.setTool('poly');
    surface.click(100, 100);
    surface.click(200, 200);
    controller.handleKey('Escape');
    expect(store.get().drawings[0]?.anchors).toHaveLength(2);
  });

  it('stops at the tool cap and places a vertex at the end of a drag', () => {
    const {controller, surface, store} = setup();
    controller.setTool('poly');
    surface.fire('pointerDown', 100, 100);
    surface.fire('pointerUp', 180, 100);
    surface.click(260, 140);
    surface.click(340, 160);
    expect(store.get().drawings[0]?.anchors).toHaveLength(4);
    expect(controller.finishDraft()).toBe(false);
  });
});

describe('freehand creation (brush)', () => {
  it('records spaced points and decimates to the tool cap', () => {
    const {controller, surface, store} = setup();
    controller.setTool('brush');
    surface.fire('pointerDown', 100, 100);
    for (let i = 1; i <= 40; i += 1)
      surface.fire('pointerMove', 100 + i * 5, 100 + (i % 3));
    surface.fire('pointerMove', 301, 101);
    surface.fire('pointerUp', 301, 101);
    const [doc] = store.get().drawings;
    expect(doc?.anchors).toHaveLength(10);
    expect(doc?.anchors[0]?.time_ms).toBe(600_000);
  });

  it('drops a click without movement', () => {
    const {controller, surface, store} = setup();
    controller.setTool('brush');
    surface.click(100, 100);
    expect(store.get().drawings).toHaveLength(0);
  });

  it('decimates evenly keeping both ends', () => {
    expect(decimate([1, 2, 3, 4, 5, 6, 7, 8, 9], 3)).toEqual([1, 5, 9]);
    expect(decimate([1, 2], 10)).toEqual([1, 2]);
    expect(MAX_ANCHORS).toBe(64);
  });
});

/**
 * Builds a drawing.
 * @param id Id.
 * @param zIndex Z index.
 * @param extra Overrides.
 * @return Document.
 */
function drawing(
  id: string,
  zIndex: number,
  extra: Partial<DrawingDocument> = {}
): DrawingDocument {
  return {
    ...createDrawing({
      toolId: 'trendline',
      toolVersion: 1,
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: null,
      anchors: [
        {time_ms: 0, price: '1'},
        {time_ms: 60_000, price: '2'},
      ],
      style: {color: '#e66fd2', line_width: 1, line_style: 'solid'},
      zIndex,
      nowMs: zIndex,
      newId: () => id,
    }),
    ...extra,
  };
}

describe('object list edits', () => {
  it('computes z-order moves with renumbering', () => {
    const list = [drawing('a', 1), drawing('b', 1), drawing('c', 7)];
    // `b` already sits at 1, so only `c` and `a` change.
    expect([...zOrderChanges(list, 'a', 'front')]).toEqual([
      ['c', 2],
      ['a', 3],
    ]);
    expect([...zOrderChanges(list, 'c', 'back')]).toEqual([
      ['c', 1],
      ['a', 2],
      ['b', 3],
    ]);
    expect(zOrderChanges(list, 'missing', 'forward').size).toBe(0);
  });

  it('reorders as one undo entry', () => {
    const store = new DrawingStore({
      drawings: [drawing('a', 1), drawing('b', 2), drawing('c', 3)],
      opaque: [],
    });
    expect(reorderDrawing(store, 'a', 'forward', 9)).toBe(true);
    expect(store.get().drawings.map(d => d.drawing_id)).toEqual([
      'b',
      'a',
      'c',
    ]);
    store.undo();
    expect(store.get().drawings.map(d => d.drawing_id)).toEqual([
      'a',
      'b',
      'c',
    ]);
    expect(reorderDrawing(store, 'c', 'front', 9)).toBe(false);
  });

  it('restyles, retexts and toggles flags with undo; locked drawings keep their style', () => {
    const store = new DrawingStore();
    store.execute(createCommand(drawing('a', 1)));
    expect(
      restyleDrawing(store, 'a', {color: '#123456', line_width: 3}, 9)
    ).toBe(true);
    expect(store.find('a')?.style).toMatchObject({
      color: '#123456',
      line_width: 3,
      line_style: 'solid',
    });
    expect(retextDrawing(store, 'a', 'x'.repeat(2100), 9)).toBe(true);
    expect(store.find('a')?.text).toHaveLength(2000);
    expect(setDrawingFlags(store, 'a', {locked: true}, 9)).toBe(true);
    expect(restyleDrawing(store, 'a', {color: '#000000'}, 10)).toBe(false);
    expect(setDrawingFlags(store, 'a', {visible: false}, 10)).toBe(true);
    expect(store.find('a')?.visible).toBe(false);
    store.undo();
    store.undo();
    store.undo();
    expect(store.find('a')?.text).toBeNull();
    expect(store.find('a')?.style.color).toBe('#123456');
  });
});

describe('tool inputs', () => {
  const inputs: ToolInput[] = [
    {
      key: 'band',
      label: 'Band',
      kind: 'choice',
      defaultValue: '1',
      choices: [
        {value: '0', label: 'Off'},
        {value: '1', label: '1σ'},
      ],
    },
    {key: 'label', label: 'Label', kind: 'boolean', defaultValue: true},
  ];

  it('reads props with defaults and rejects unknown choices', () => {
    expect(inputDefaults(inputs)).toEqual({band: '1', label: true});
    const [band, label] = inputs;
    if (!band || !label) throw new Error('inputs');
    expect(readInput({props: {band: '0'}}, band)).toBe('0');
    expect(readInput({props: {band: '7'}}, band)).toBe('1');
    expect(readInput({props: {label: 'yes'}}, label)).toBe(true);
    expect(readInput({}, label)).toBe(true);
  });

  it('changes inputs as one undoable entry, never on locked drawings', () => {
    const store = new DrawingStore();
    store.execute(createCommand(drawing('a', 1, {props: {band: '1'}})));
    expect(setDrawingInputs(store, 'a', {band: '0'}, 9)).toBe(true);
    expect(store.find('a')?.props).toEqual({band: '0'});
    store.undo();
    expect(store.find('a')?.props).toEqual({band: '1'});
    setDrawingFlags(store, 'a', {locked: true}, 10);
    expect(setDrawingInputs(store, 'a', {band: '0'}, 11)).toBe(false);
  });
});
