/**
 * @fileoverview Tests for the first-party tool list, plus contract checks
 * every tool must pass: schema-valid documents that survive persistence,
 * undoable edits, locking, visibility and anchor-indexed handles.
 */
import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import type {DrawingAnchor} from '@joseph/contracts';
import {
  DrawingRepository,
  DrawingStore,
  ToolRegistry,
  createCommand,
  restyleDrawing,
  setDrawingFlags,
  type ToolPlugin,
} from '@joseph/drawing-core';
import {describe, expect, it} from 'vitest';
import {FIRST_PARTY_TOOLS} from '.';
import {TRANSFORM, makeDoc} from './testing/harness';

/** Tools added in the extended (P3) tier and the engine-backed tools. */
const EXTENDED_TOOLS = [
  'ellipse',
  'circle',
  'triangle',
  'rotated-rectangle',
  'arc',
  'curve',
  'gann-fan',
  'gann-box',
  'fib-time-zones',
  'fib-circles',
  'pitchfork',
  'xabcd-pattern',
  'abcd-pattern',
  'head-and-shoulders',
  'elliott-impulse',
  'elliott-correction',
  'triangle-pattern',
  'anchored-vwap',
  'regression-channel',
];

/**
 * Builds the contract validator for `DrawingDocument`.
 * @return Validation function.
 */
function drawingValidator(): (value: unknown) => boolean {
  const dir = join(__dirname, '..', '..', 'contracts', 'schemas');
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const file of readdirSync(dir)) {
    if (file.endsWith('.schema.json'))
      ajv.addSchema(JSON.parse(readFileSync(join(dir, file), 'utf8')));
  }
  const validate = ajv.getSchema(
    'https://schemas.joseph.local/v1/workspace.schema.json#/$defs/DrawingDocument'
  );
  if (!validate) throw new Error('missing DrawingDocument');
  return value => validate(value) as boolean;
}

/**
 * Zig-zag anchors a user could place for a tool.
 * @param tool Tool.
 * @return Anchors (5 minutes apart, alternating prices).
 */
function anchorsFor(tool: ToolPlugin): DrawingAnchor[] {
  const mode = tool.manifest.creation ?? 'anchors';
  const count =
    mode === 'anchors'
      ? tool.manifest.anchorCount
      : Math.max(2, tool.manifest.anchorCount);
  return Array.from({length: count}, (_, i) => ({
    time_ms: (5 + i * 5) * 60_000,
    price: String(i % 2 === 0 ? 140 + i : 165 - i),
  }));
}

describe('FIRST_PARTY_TOOLS', () => {
  it('registers every tool with a unique id', () => {
    const registry = new ToolRegistry();
    for (const tool of FIRST_PARTY_TOOLS) registry.register(tool);
    expect(registry.list()).toHaveLength(37);
    expect(registry.list().map(t => t.manifest.id)).toEqual(
      expect.arrayContaining([
        'ray',
        'extended-line',
        'arrow',
        'polyline',
        'brush',
        'zone-box',
        ...EXTENDED_TOOLS,
      ])
    );
  });

  it('removing a tool leaves the others working', () => {
    const registry = new ToolRegistry();
    const disposers = FIRST_PARTY_TOOLS.map(tool => registry.register(tool));
    disposers[
      FIRST_PARTY_TOOLS.findIndex(t => t.manifest.id === 'fib-retracement')
    ]?.();
    expect(registry.get('fib-retracement')).toBeUndefined();
    expect(registry.get('trendline')).toBeDefined();
  });

  it('keeps each toolbar group contiguous', () => {
    const groups = FIRST_PARTY_TOOLS.map(t => t.manifest.group);
    const seen = new Set<string>();
    groups.forEach((group, i) => {
      if (i > 0 && groups[i - 1] !== group) {
        expect(seen.has(group)).toBe(false);
      }
      seen.add(group);
    });
  });
});

describe.each(FIRST_PARTY_TOOLS.map(tool => [tool.manifest.id, tool]))(
  'tool contract: %s',
  (_id, tool) => {
    const validate = drawingValidator();

    it('creates schema-valid documents that survive save and load', () => {
      const doc = makeDoc(tool, anchorsFor(tool));
      expect(validate(doc)).toBe(true);
      const items = new Map<string, string>();
      const repository = new DrawingRepository(
        {
          getItem: key => items.get(key) ?? null,
          setItem: (key, value) => void items.set(key, value),
        },
        'default'
      );
      repository.save('mock:perp:BTCUSD', {drawings: [doc], opaque: []});
      const loaded = repository.load('mock:perp:BTCUSD');
      expect(loaded.opaque).toEqual([]);
      expect(loaded.drawings).toEqual([doc]);
    });

    it('undoes creation and restyling, and honours lock and visibility', () => {
      const doc = makeDoc(tool, anchorsFor(tool));
      const store = new DrawingStore();
      store.execute(createCommand(doc));
      expect(restyleDrawing(store, doc.drawing_id, {line_width: 3}, 1)).toBe(
        true
      );
      setDrawingFlags(store, doc.drawing_id, {locked: true}, 2);
      expect(restyleDrawing(store, doc.drawing_id, {line_width: 4}, 3)).toBe(
        false
      );
      expect(store.remove(doc.drawing_id)).toBe(false);
      setDrawingFlags(store, doc.drawing_id, {visible: false}, 4);
      expect(store.find(doc.drawing_id)?.visible).toBe(false);
      store.undo();
      store.undo();
      expect(store.find(doc.drawing_id)?.locked).toBe(false);
      store.undo();
      expect(store.find(doc.drawing_id)?.style.line_width).toBe(
        doc.style.line_width
      );
      store.undo();
      expect(store.find(doc.drawing_id)).toBeUndefined();
      store.redo();
      expect(store.find(doc.drawing_id)).toEqual(doc);
    });

    if (EXTENDED_TOOLS.includes(tool.manifest.id)) {
      it('maps every handle to its anchor index', () => {
        const doc = makeDoc(tool, anchorsFor(tool));
        doc.anchors.forEach((anchor, index) => {
          const x = TRANSFORM.timeToX(anchor.time_ms) ?? 0;
          const y = TRANSFORM.priceToY(Number(anchor.price)) ?? 0;
          expect(tool.hitTest(doc, TRANSFORM, {x, y}, 6, null)).toEqual({
            part: 'handle',
            handleIndex: index,
          });
        });
      });
    }
  }
);
