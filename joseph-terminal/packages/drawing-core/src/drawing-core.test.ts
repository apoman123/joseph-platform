/**
 * @fileoverview Tests for drawing documents, history, geometry, snapping,
 * migrations, persistence and the interaction controller.
 */
import type {DrawingDocument} from '@joseph/contracts';
import {describe, expect, it, vi} from 'vitest';
import {DrawingController} from './controller';
import {anchorFromPoint, createDrawing, translateDrawing} from './document';
import {
  distanceToSegment,
  extendSegment,
  handleAt,
  pointInRect,
  rectFromPoints,
} from './geometry';
import {MigrationRegistry} from './migrations';
import {DrawingRepository, bindAutosave} from './persistence';
import {magnetSnap} from './snapping';
import {DrawingStore, createCommand} from './store';
import type {
  DrawingSurface,
  PixelTransform,
  SurfaceCanvasLayer,
  SurfaceEventMap,
  SurfacePointerEvent,
} from './surface';
import {ToolRegistry, formatPrice, type ToolPlugin} from './tool';

/** Linear transform: 10px per minute from t=0; price 200 at y=0, 100 at y=500. */
const TRANSFORM: PixelTransform = {
  width: 1000,
  height: 500,
  timeToX: t => (t / 60_000) * 10,
  xToTime: x => (x / 10) * 60_000,
  priceToY: p => ((200 - p) / 100) * 500,
  yToPrice: y => 200 - (y / 500) * 100,
};

/** Minimal in-memory surface. */
class TestSurface implements DrawingSurface {
  interactive = true;
  layers: SurfaceCanvasLayer[] = [];
  handlers: {
    [K in keyof SurfaceEventMap]?: ((e: SurfaceEventMap[K]) => void)[];
  } = {};

  /**
   * Subscribes.
   * @param event Event.
   * @param handler Handler.
   * @return Disposer.
   */
  on<E extends keyof SurfaceEventMap>(
    event: E,
    handler: (payload: SurfaceEventMap[E]) => void
  ): () => void {
    const list = (this.handlers[event] ??= []) as ((
      e: SurfaceEventMap[E]
    ) => void)[];
    list.push(handler);
    return () => {
      list.splice(list.indexOf(handler), 1);
    };
  }

  /**
   * Adds a layer.
   * @param layer Layer.
   * @return Disposer.
   */
  addCanvasLayer(layer: SurfaceCanvasLayer): () => void {
    this.layers.push(layer);
    return () => {
      this.layers = this.layers.filter(l => l !== layer);
    };
  }

  /** No-op. */
  invalidate(): void {}

  /**
   * Records interactivity.
   * @param enabled Flag.
   */
  setInteractive(enabled: boolean): void {
    this.interactive = enabled;
  }

  /**
   * Returns the transform.
   * @return Transform.
   */
  transform(): PixelTransform {
    return TRANSFORM;
  }

  /**
   * Fires a pointer event.
   * @param kind Event kind.
   * @param x X.
   * @param y Y.
   * @return The event.
   */
  fire(
    kind: keyof SurfaceEventMap,
    x: number,
    y: number
  ): SurfacePointerEvent & {prevented: boolean} {
    const event = {
      x,
      y,
      timeMs: TRANSFORM.xToTime(x),
      price: TRANSFORM.yToPrice(y),
      button: 0,
      shiftKey: false,
      modKey: false,
      prevented: false,
      preventDefault() {
        event.prevented = true;
      },
    };
    for (const handler of [...(this.handlers[kind] ?? [])]) {
      (handler as (e: SurfacePointerEvent) => void)(event);
    }
    return event;
  }
}

/** Two-anchor segment tool used by controller tests. */
const SEGMENT_TOOL: ToolPlugin = {
  manifest: {
    id: 'segment',
    version: 1,
    displayName: 'Segment',
    group: 'lines',
    icon: 'minus',
    anchorCount: 2,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
  }),
  render: (ctx, doc, transform) => {
    ctx.beginPath();
    for (const a of doc.anchors)
      ctx.lineTo(
        transform.timeToX(a.time_ms) ?? 0,
        transform.priceToY(Number(a.price)) ?? 0
      );
    ctx.stroke();
  },
  hitTest: (doc, transform, point, tolerance) => {
    const [a, b] = doc.anchors.map(anchor => ({
      x: transform.timeToX(anchor.time_ms) ?? 0,
      y: transform.priceToY(Number(anchor.price)) ?? 0,
    }));
    if (!a || !b) return null;
    const handle = handleAt(point, [a, b], tolerance);
    if (handle >= 0) return {part: 'handle', handleIndex: handle};
    return distanceToSegment(point, a, b) <= tolerance ? {part: 'body'} : null;
  },
};

/**
 * Builds a document.
 * @param id Id.
 * @param extra Overrides.
 * @return Document.
 */
function doc(
  id: string,
  extra: Partial<DrawingDocument> = {}
): DrawingDocument {
  return {
    ...createDrawing({
      toolId: 'segment',
      toolVersion: 1,
      instrumentId: 'mock:perp:BTCUSD',
      timeframe: null,
      anchors: [
        {time_ms: 0, price: '150'},
        {time_ms: 600_000, price: '160'},
      ],
      style: {color: '#e66fd2', line_width: 1, line_style: 'solid'},
      zIndex: 1,
      nowMs: 1,
      newId: () => id,
    }),
    ...extra,
  };
}

/**
 * Builds a controller on a test surface.
 * @param store Store.
 * @return Controller and surface.
 */
function setup(store = new DrawingStore()) {
  const surface = new TestSurface();
  const tools = new ToolRegistry();
  tools.register(SEGMENT_TOOL);
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
      text: '#888',
      bid: '#0f0',
      ask: '#f00',
      background: '#000',
      fontFamily: 'sans',
    }),
    now: () => 5,
    newId: () => `d${++seq}`,
  });
  return {surface, tools, controller, store};
}

describe('geometry', () => {
  it('measures distance to segments and rects', () => {
    expect(distanceToSegment({x: 5, y: 5}, {x: 0, y: 0}, {x: 10, y: 0})).toBe(
      5
    );
    expect(
      pointInRect({x: 2, y: 2}, rectFromPoints({x: 4, y: 4}, {x: 0, y: 0}))
    ).toBe(true);
    expect(
      handleAt(
        {x: 11, y: 0},
        [
          {x: 0, y: 0},
          {x: 10, y: 0},
        ],
        3
      )
    ).toBe(1);
  });

  it('extends segments to the plot edges', () => {
    const [left, right] = extendSegment(
      {x: 10, y: 10},
      {x: 20, y: 20},
      100,
      true,
      true
    );
    expect(left).toEqual({x: 0, y: 0});
    expect(right).toEqual({x: 100, y: 100});
  });
});

describe('documents', () => {
  it('stores anchors as tick-rounded decimal strings', () => {
    expect(anchorFromPoint(TRANSFORM, {x: 10, y: 250}, '0.5')).toEqual({
      time_ms: 60_000,
      price: '150',
    });
    expect(anchorFromPoint(TRANSFORM, {x: 10, y: 251}, '0.1')?.price).toBe(
      '149.8'
    );
  });

  it('translates anchors exactly', () => {
    const moved = translateDrawing(doc('a'), 60_000, '0.1', 9);
    expect(moved.anchors[0]).toEqual({time_ms: 60_000, price: '150.1'});
    expect(moved.updated_at_ms).toBe(9);
  });

  it('formats label prices with grouping', () => {
    expect(formatPrice('94700.5', '0.1')).toBe('94,700.5');
    // Half-even rounding: -1234.5 rounds to the even neighbour.
    expect(formatPrice(-1234.5, '1')).toBe('−1,234');
  });
});

describe('DrawingStore', () => {
  it('undoes and redoes create/update/delete', () => {
    const store = new DrawingStore();
    store.execute(createCommand(doc('a')));
    store.update(
      'a',
      d => ({...d, style: {...d.style, color: '#000000'}}),
      'Recolor'
    );
    store.remove('a');
    expect(store.get().drawings).toHaveLength(0);
    store.undo();
    expect(store.find('a')?.style.color).toBe('#000000');
    store.undo();
    expect(store.find('a')?.style.color).toBe('#e66fd2');
    store.redo();
    expect(store.find('a')?.style.color).toBe('#000000');
    store.undo();
    store.undo();
    expect(store.get().drawings).toHaveLength(0);
    expect(store.canUndo()).toBe(false);
  });

  it('merges a drag gesture into one history entry', () => {
    const store = new DrawingStore();
    store.execute(createCommand(doc('a')));
    for (let i = 1; i <= 5; i += 1) {
      store.update(
        'a',
        d => translateDrawing(d, 0, '1', i),
        'Move',
        'drag:a:1'
      );
    }
    expect(store.find('a')?.anchors[0]?.price).toBe('155');
    store.undo();
    expect(store.find('a')?.anchors[0]?.price).toBe('150');
  });

  it('refuses to move or delete locked drawings but allows unlocking', () => {
    const store = new DrawingStore({
      drawings: [doc('a', {locked: true})],
      opaque: [],
    });
    expect(store.update('a', d => translateDrawing(d, 0, '1', 1), 'Move')).toBe(
      false
    );
    expect(store.remove('a')).toBe(false);
    expect(store.update('a', d => ({...d, visible: false}), 'Hide')).toBe(true);
    expect(store.update('a', d => ({...d, locked: false}), 'Unlock')).toBe(
      true
    );
    expect(store.remove('a')).toBe(true);
  });
});

describe('magnet snapping', () => {
  it('snaps to the nearest OHLC within the radius', () => {
    const bars = [{timeMs: 0, open: 150, high: 160, low: 140, close: 155}];
    const snapped = magnetSnap({x: 3, y: 205}, bars, TRANSFORM, 60_000);
    expect(snapped.y).toBe(200);
    expect(magnetSnap({x: 3, y: 120}, bars, TRANSFORM, 60_000).y).toBe(120);
  });
});

describe('migrations', () => {
  const registry = new MigrationRegistry();

  it('migrates legacy v0 numeric points to v1 decimal anchors', () => {
    const outcome = registry.read({
      id: 'old',
      tool: 'horizontal-line',
      instrument: 'mock:perp:BTCUSD',
      points: [{t: 1000, p: 94700.5}],
    });
    expect(outcome.kind).toBe('document');
    if (outcome.kind === 'document') {
      expect(outcome.migrated).toBe(true);
      expect(outcome.doc.anchors).toEqual([{time_ms: 1000, price: '94700.5'}]);
    }
  });

  it('keeps newer-schema documents opaque', () => {
    const future = {schema_version: 9, drawing_id: 'x'};
    expect(registry.read(future)).toEqual({
      kind: 'opaque',
      raw: future,
      reason: expect.stringContaining('newer'),
    });
  });
});

describe('persistence', () => {
  it('round-trips drawings per exact instrument and preserves opaque payloads', () => {
    const memory = new Map<string, string>();
    const storage = {
      getItem: (k: string) => memory.get(k) ?? null,
      setItem: (k: string, v: string) => void memory.set(k, v),
    };
    const repo = new DrawingRepository(storage, 'ws1');
    const future = {schema_version: 9, drawing_id: 'future'};
    repo.save('mock:perp:BTCUSD', {drawings: [doc('a')], opaque: [future]});
    const loaded = repo.load('mock:perp:BTCUSD');
    expect(loaded.drawings.map(d => d.drawing_id)).toEqual(['a']);
    expect(loaded.opaque).toEqual([future]);
    expect(repo.load('mock:perp:ETHUSD').drawings).toEqual([]);
    repo.save('mock:perp:BTCUSD', loaded);
    expect(repo.load('mock:perp:BTCUSD').opaque).toEqual([future]);
  });

  it('autosaves after changes and flushes on dispose', () => {
    vi.useFakeTimers();
    const saved: string[] = [];
    const repo = new DrawingRepository(
      {getItem: () => null, setItem: (_k, v) => void saved.push(v)},
      'ws'
    );
    const store = new DrawingStore();
    const dispose = bindAutosave(store, repo, 'mock:perp:BTCUSD', 100);
    store.execute(createCommand(doc('a')));
    store.execute(createCommand(doc('b')));
    expect(saved).toHaveLength(0);
    vi.advanceTimersByTime(100);
    expect(saved).toHaveLength(1);
    store.remove('a');
    dispose();
    expect(saved).toHaveLength(2);
    vi.useRealTimers();
  });
});

describe('DrawingController', () => {
  it('creates a drawing with press-drag-release and disarms the tool', () => {
    const {surface, controller, store} = setup();
    controller.setTool('segment');
    expect(surface.interactive).toBe(false);
    const down = surface.fire('pointerDown', 0, 250);
    expect(down.prevented).toBe(true);
    surface.fire('pointerMove', 100, 200);
    surface.fire('pointerUp', 100, 200);
    expect(store.get().drawings).toHaveLength(1);
    expect(store.get().drawings[0]?.anchors).toEqual([
      {time_ms: 0, price: '150'},
      {time_ms: 600_000, price: '160'},
    ]);
    expect(controller.activeTool).toBeNull();
    expect(surface.interactive).toBe(true);
  });

  it('creates with click-per-anchor', () => {
    const {surface, controller, store} = setup();
    controller.setTool('segment');
    surface.fire('pointerDown', 0, 250);
    surface.fire('pointerUp', 0, 250);
    expect(store.get().drawings).toHaveLength(0);
    surface.fire('pointerMove', 50, 250);
    surface.fire('pointerDown', 50, 250);
    surface.fire('pointerUp', 50, 250);
    expect(store.get().drawings).toHaveLength(1);
  });

  it('drags a handle as one undoable gesture', () => {
    const store = new DrawingStore({drawings: [doc('a')], opaque: []});
    const {surface, controller} = setup(store);
    surface.fire('pointerDown', 100, 200);
    expect(controller.selection).toBe('a');
    surface.fire('pointerMove', 120, 200);
    surface.fire('pointerMove', 150, 150);
    surface.fire('pointerUp', 150, 150);
    expect(store.find('a')?.anchors[1]).toEqual({
      time_ms: 900_000,
      price: '170',
    });
    store.undo();
    expect(store.find('a')?.anchors[1]).toEqual({
      time_ms: 600_000,
      price: '160',
    });
  });

  it('deletes the selection with the keyboard and escapes drafts', () => {
    const store = new DrawingStore({drawings: [doc('a')], opaque: []});
    const {surface, controller} = setup(store);
    surface.fire('pointerDown', 50, 225);
    surface.fire('pointerUp', 50, 225);
    expect(controller.handleKey('Delete')).toBe(true);
    expect(store.get().drawings).toHaveLength(0);
    controller.setTool('segment');
    surface.fire('pointerDown', 0, 250);
    expect(controller.handleKey('Escape')).toBe(true);
    expect(controller.handleKey('Escape')).toBe(true);
    expect(controller.activeTool).toBeNull();
  });

  it('renders unknown-tool drawings as placeholders and never deletes them', () => {
    const orphan = doc('orphan', {tool_id: 'removed-tool'});
    const store = new DrawingStore({drawings: [orphan], opaque: []});
    const {surface} = setup(store);
    const texts: string[] = [];
    const ctx = new Proxy({} as Record<string, unknown>, {
      get: (_t, prop: string) =>
        prop === 'fillText'
          ? (text: string) => texts.push(text)
          : () => undefined,
      set: () => true,
    }) as unknown as CanvasRenderingContext2D;
    surface.layers[0]?.draw(ctx, TRANSFORM);
    expect(texts).toEqual(['Unsupported drawing (removed-tool)']);
    surface.fire('pointerDown', 0, 250);
    surface.fire('pointerMove', 300, 100);
    surface.fire('pointerUp', 300, 100);
    expect(store.find('orphan')?.anchors).toEqual(orphan.anchors);
  });

  it('removes all listeners on dispose', () => {
    const {surface, controller} = setup();
    controller.dispose();
    expect(
      Object.values(surface.handlers).every(list => list?.length === 0)
    ).toBe(true);
    expect(surface.layers).toHaveLength(0);
  });
});
