/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Placement, identity, editing and persistence of upstream tools. */

import {describe, expect, it} from 'vitest';
import {
  DrawingStore,
  createCommand,
  createDrawing,
  type PixelTransform,
  type ToolGeometryContext,
} from '@joseph/drawing-core';
import type {DrawingDocument} from '@joseph/contracts';
import {REFERENCE_TOOLS, referenceDrawing} from './index';

/** Stable finite chart coordinates: one bar every ten pixels. */
const transform: PixelTransform = {
  width: 1000,
  height: 500,
  timeToX: time => (time - 1_795_000_020_000) / 6000,
  xToTime: x => 1_795_000_020_000 + x * 6000,
  priceToY: price => (200 - price) * 5,
  yToPrice: y => 200 - y / 5,
};

/** Real-valued, bounded fixture bars; no generated values enter production. */
const context: ToolGeometryContext = {
  transform,
  durationMs: 60_000,
  tickSize: '0.01',
  bars: Array.from({length: 100}, (_, index) => ({
    timeMs: 1_795_000_020_000 + index * 60_000,
    open: 120 + index / 10,
    high: 125 + index / 10,
    low: 115 + index / 10,
    close: 122 + index / 10,
    volume: 100 + index,
  })),
};

/**
 * Creates a canonical document using the source's actual placement rules.
 * @param id Registered upstream tool id.
 * @return Placed canonical document retaining the exact instrument identity.
 * @throws Error when the requested catalog entry is absent.
 */
function placed(id: string): DrawingDocument {
  const tool = REFERENCE_TOOLS.find(item => item.manifest.id === id);
  if (!tool) throw new Error(`Missing tool ${id}`);
  const anchors = Array.from(
    {length: tool.manifest.anchorCount},
    (_, index) => ({
      time_ms: 1_795_000_620_000 + index * 300_000,
      price: String(125 + index * 3),
    })
  );
  const palette = {
    user: '#5294ff',
    text: '#eeeeee',
    bid: '#009988',
    ask: '#dd3344',
    background: '#111111',
    fontFamily: 'system-ui',
  };
  const patch = tool.place?.(anchors, context);
  expect(patch).not.toBeNull();
  return {
    ...createDrawing({
      toolId: id,
      toolVersion: 1,
      instrumentId: 'binance:perp:BTCUSDT',
      timeframe: null,
      anchors,
      style: tool.defaultStyle(palette),
      props: tool.defaultProps?.(),
      zIndex: 1,
      nowMs: 1_795_000_000_000,
      newId: () => `canonical-${id}`,
    }),
    ...patch,
  };
}

describe('TradeX drawing core through canonical Joseph documents', () => {
  it('distinguishes geometry icons and retains the native highlighter width', () => {
    const icons = new Map(
      REFERENCE_TOOLS.map(tool => [tool.manifest.id, tool.manifest.icon])
    );
    expect(new Set(icons.values()).size).toBeGreaterThan(15);
    expect(icons.get('reference.rectangle')).not.toBe(
      icons.get('reference.trend-line')
    );
    expect(icons.get('reference.ellipse')).not.toBe(
      icons.get('reference.trend-line')
    );
    const highlighter = placed('reference.highlighter');
    expect(highlighter.style.line_width).toBe(20);
    const restored = JSON.parse(JSON.stringify(highlighter)) as DrawingDocument;
    expect(referenceDrawing('highlighter', restored)?.style.width).toBe(20);
  });

  it('ships all 86 unique namespaced tools without reinterpreting legacy documents', () => {
    expect(REFERENCE_TOOLS).toHaveLength(86);
    expect(new Set(REFERENCE_TOOLS.map(tool => tool.manifest.id)).size).toBe(
      86
    );
    expect(
      REFERENCE_TOOLS.every(tool => tool.manifest.id.startsWith('reference.'))
    ).toBe(true);
  });

  it.each(REFERENCE_TOOLS)(
    'places and serializes $manifest.displayName using native rules',
    tool => {
      const doc = placed(tool.manifest.id);
      expect(doc.anchors.length).toBeGreaterThan(0);
      expect(doc.anchors.length).toBeLessThanOrEqual(64);
      expect(
        doc.anchors.every(
          anchor =>
            Number.isFinite(anchor.time_ms) &&
            Number.isFinite(Number(anchor.price))
        )
      ).toBe(true);
      expect(doc.instrument_id).toBe('binance:perp:BTCUSDT');
      const store = new DrawingStore();
      store.execute(createCommand(doc));
      expect(store.get().drawings[0]).toEqual(JSON.parse(JSON.stringify(doc)));
      store.undo();
      expect(store.get().drawings).toHaveLength(0);
      store.redo();
      expect(store.get().drawings[0]?.anchors).toEqual(doc.anchors);
    }
  );

  it('retains derived curve control points and source-specific risk offsets', () => {
    const curve = placed('reference.curve');
    expect(curve.anchors).toHaveLength(3);
    const position = placed('reference.long-position');
    expect(position.anchors).toHaveLength(2);
    const props = position.props as Record<string, unknown>;
    expect(props.reference_style).toBeDefined();
    expect(position.anchors[0]?.time_ms).not.toBe(position.anchors[1]?.time_ms);
  });

  it('edits geometry without changing identity or mutating the original document', () => {
    const tool = REFERENCE_TOOLS.find(
      item => item.manifest.id === 'reference.rectangle'
    );
    const original = placed('reference.rectangle');
    const copy = structuredClone(original);
    const changed = tool?.edit?.(
      original,
      {
        handleIndex: 0,
        start: {x: 100, y: 375},
        current: {x: 90, y: 360},
        constrain: false,
      },
      context
    );
    expect(changed?.anchors).not.toEqual(original.anchors);
    expect(original).toEqual(copy);
    expect({...original, ...changed}.drawing_id).toBe(copy.drawing_id);
  });

  it('validates stored native payloads and discards non-finite geometry', () => {
    const doc = placed('reference.trend-line');
    expect(referenceDrawing('trend-line', doc)?.points).toHaveLength(2);
    const corrupt = {
      ...doc,
      anchors: [{time_ms: NaN, price: '100'}, ...doc.anchors.slice(1)],
    };
    expect(referenceDrawing('trend-line', corrupt)).toBeNull();
  });
});
