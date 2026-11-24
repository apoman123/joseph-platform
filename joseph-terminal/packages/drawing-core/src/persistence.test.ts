/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Drawing export and import for workspace files: listing the
 * workspace's instruments, export limits, defensive reading of untrusted
 * collections (migrations, contract limits, opaque newer-schema payloads,
 * unknown tools kept) and per-instrument restore.
 */

import type {DrawingDocument} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  DrawingRepository,
  MAX_EXPORT_DRAWINGS,
  isImportableDrawing,
  readImportedDrawings,
  upgradeToolVersion,
  type DrawingStorage,
} from './persistence';
import {ToolRegistry, type ToolPlugin} from './tool';

const BTC = 'mock:perp:BTCUSD';
const ETH = 'mock:perp:ETHUSD';

/**
 * Builds a valid drawing document.
 * @param id Drawing id.
 * @param extra Overrides.
 * @return Document.
 */
function doc(
  id: string,
  extra: Partial<DrawingDocument> = {}
): DrawingDocument {
  return {
    schema_version: 1,
    drawing_id: id,
    tool_id: 'trendline',
    tool_version: 1,
    instrument_id: BTC,
    timeframe: null,
    anchors: [
      {time_ms: 1_000, price: '100'},
      {time_ms: 2_000, price: '110'},
    ],
    style: {color: '#e66fd2', line_width: 1, line_style: 'solid'},
    text: null,
    props: {},
    locked: false,
    visible: true,
    z_index: 0,
    created_at_ms: 1,
    updated_at_ms: 1,
    ...extra,
  };
}

/**
 * In-memory storage with key listing.
 * @return Storage and its map.
 */
function memoryStorage(): {
  storage: Required<DrawingStorage>;
  map: Map<string, string>;
} {
  const map = new Map<string, string>();
  return {
    map,
    storage: {
      getItem: key => map.get(key) ?? null,
      setItem: (key, value) => void map.set(key, value),
      keys: prefix => [...map.keys()].filter(key => key.startsWith(prefix)),
    },
  };
}

/** Tool whose version 2 renames a prop (exercises tool migrations). */
const TRENDLINE_V2: ToolPlugin = {
  manifest: {
    id: 'trendline',
    version: 2,
    displayName: 'Trendline',
    group: 'lines',
    icon: 'minus',
    anchorCount: 2,
  },
  defaultStyle: () => ({color: '#ffffff', line_width: 1, line_style: 'solid'}),
  render: () => undefined,
  hitTest: () => null,
  migrate: d => ({...d, tool_version: 2, props: {...d.props, upgraded: true}}),
};

describe('drawing export', () => {
  it('lists the instruments of one workspace only', () => {
    const {storage, map} = memoryStorage();
    const repo = new DrawingRepository(storage, 'ws1');
    repo.save(ETH, {drawings: [doc('e', {instrument_id: ETH})], opaque: []});
    repo.save(BTC, {drawings: [doc('b')], opaque: []});
    new DrawingRepository(storage, 'other').save(BTC, {
      drawings: [doc('x')],
      opaque: [],
    });
    map.set('joseph.drawings.v1.ws1.not-an-id', '{}');
    expect(repo.instruments()).toEqual([BTC, ETH]);
    const noKeys = new DrawingRepository(
      {getItem: storage.getItem, setItem: storage.setItem},
      'ws1'
    );
    expect(noKeys.instruments()).toEqual([]);
  });

  it('exports drawings and object payloads, skipping empty collections', () => {
    const {storage, map} = memoryStorage();
    const repo = new DrawingRepository(storage, 'ws1');
    const future = {schema_version: 7, drawing_id: 'future'};
    repo.save(BTC, {drawings: [doc('a'), doc('b')], opaque: [future]});
    repo.save(ETH, {drawings: [], opaque: []});
    // Unparseable text stays local: it is not an object payload.
    map.set('joseph.drawings.v1.ws1.mock:perp:SOLUSD', '{broken');
    const exported = repo.exportAll();
    expect(exported.drawings).toBe(2);
    expect(exported.omitted).toBe(0);
    expect(exported.collections).toEqual([
      {instrument_id: BTC, drawings: [doc('a'), doc('b')], opaque: [future]},
    ]);
  });

  it('caps drawings per instrument and reports what stayed behind', () => {
    const {storage} = memoryStorage();
    const repo = new DrawingRepository(storage, 'ws1');
    const many = Array.from({length: MAX_EXPORT_DRAWINGS + 3}, (_, i) =>
      doc(`d${i}`)
    );
    repo.save(BTC, {drawings: many, opaque: []});
    const exported = repo.exportAll();
    expect(exported.collections[0]?.drawings).toHaveLength(MAX_EXPORT_DRAWINGS);
    expect(exported.omitted).toBe(3);
    expect(exported.collections[0]).not.toHaveProperty('opaque');
  });
});

describe('drawing import', () => {
  it('keeps readable documents, unknown tools and newer-schema payloads', () => {
    const future = {schema_version: 5, drawing_id: 'f', shape: 'new'};
    const legacy = {
      id: 'old',
      tool: 'horizontal-line',
      instrument: BTC,
      points: [{t: 5_000, p: 101.5}],
    };
    const result = readImportedDrawings([
      {
        instrument_id: BTC,
        drawings: [
          doc('a'),
          doc('u', {tool_id: 'removed-tool'}),
          future,
          legacy,
        ],
        opaque: [{schema_version: 9, drawing_id: 'g'}],
      },
    ]);
    expect(result.dropped).toBe(0);
    expect(result.drawings).toBe(3);
    expect(result.opaque).toBe(2);
    const [collection] = result.collections;
    expect(collection?.instrumentId).toBe(BTC);
    expect(collection?.state.drawings.map(d => d.drawing_id)).toEqual([
      'a',
      'u',
      'old',
    ]);
    expect(collection?.state.drawings[2]?.anchors).toEqual([
      {time_ms: 5_000, price: '101.5'},
    ]);
    expect(collection?.state.opaque).toEqual([
      future,
      {schema_version: 9, drawing_id: 'g'},
    ]);
  });

  it('drops malformed entries and counts them', () => {
    const tooMany = Array.from({length: 65}, (_, i) => ({
      time_ms: i,
      price: '1',
    }));
    const result = readImportedDrawings([
      {
        instrument_id: BTC,
        drawings: [
          doc('ok'),
          doc('ok'),
          doc('eth', {instrument_id: ETH}),
          doc('anchors', {anchors: tooMany}),
          // Deliberately invalid values; assertions bypass the contract types.
          doc('color', {
            style: {color: 'red', line_width: 1, line_style: 'solid'},
          }),
          doc('width', {
            style: {color: '#ffffff', line_width: 9, line_style: 'solid'},
          }),
          doc('dash', {
            style: {
              color: '#ffffff',
              line_width: 1,
              line_style: 'wavy' as 'solid',
            },
          }),
          doc('tf', {timeframe: '7m' as never}),
          doc('text', {text: 'x'.repeat(2001)}),
          doc('', {}),
          doc('price', {anchors: [{time_ms: 1, price: 'abc'}]}),
          doc('time', {anchors: [{time_ms: -1, price: '1'}]}),
          {schema_version: 1, drawing_id: 'missing-fields'},
          'not an object',
        ],
        opaque: ['text payload', {schema_version: 3}],
      },
      {instrument_id: 'nonsense', drawings: [doc('x'), doc('y')]},
      {instrument_id: BTC, drawings: [doc('dup')]},
      42,
    ]);
    expect(result.collections).toHaveLength(1);
    expect(
      result.collections[0]?.state.drawings.map(d => d.drawing_id)
    ).toEqual(['ok']);
    expect(result.collections[0]?.state.opaque).toEqual([{schema_version: 3}]);
    // 13 bad drawings + 1 bad payload + 2 (bad id) + 1 (duplicate) + 1 (42).
    expect(result.dropped).toBe(18);
    expect(result.drawings).toBe(1);
  });

  it('applies the contract limits to a single document', () => {
    expect(isImportableDrawing(doc('a'), BTC)).toBe(true);
    expect(isImportableDrawing(doc('a', {timeframe: '1h'}), BTC)).toBe(true);
    expect(isImportableDrawing(doc('a', {props: [] as never}), BTC)).toBe(
      false
    );
    expect(isImportableDrawing(doc('a', {tool_version: 0}), BTC)).toBe(false);
    expect(isImportableDrawing(doc('a', {z_index: Infinity}), BTC)).toBe(false);
    expect(isImportableDrawing(doc('a'), ETH)).toBe(false);
  });

  it('upgrades documents of tools with a newer version', () => {
    const tools = new ToolRegistry();
    tools.register(TRENDLINE_V2);
    const result = readImportedDrawings(
      [{instrument_id: BTC, drawings: [doc('a')]}],
      {tools}
    );
    expect(result.collections[0]?.state.drawings[0]).toMatchObject({
      tool_version: 2,
      props: {upgraded: true},
    });
    const failing: ToolPlugin = {
      ...TRENDLINE_V2,
      migrate: () => {
        throw new Error('broken');
      },
    };
    const broken = new ToolRegistry();
    broken.register(failing);
    expect(upgradeToolVersion(doc('a'), broken).tool_version).toBe(1);
    expect(upgradeToolVersion(doc('a'), undefined)).toEqual(doc('a'));
  });

  it('restores each instrument, replacing only the instruments in the file', () => {
    const {storage} = memoryStorage();
    const repo = new DrawingRepository(storage, 'ws1');
    repo.save(BTC, {drawings: [doc('mine')], opaque: []});
    repo.save(ETH, {drawings: [doc('keep', {instrument_id: ETH})], opaque: []});
    const imported = readImportedDrawings([
      {instrument_id: BTC, drawings: [doc('theirs')]},
    ]);
    repo.restore(imported.collections);
    expect(repo.load(BTC).drawings.map(d => d.drawing_id)).toEqual(['theirs']);
    expect(repo.load(ETH).drawings.map(d => d.drawing_id)).toEqual(['keep']);
    // A round trip through export and import is lossless.
    const again = readImportedDrawings(repo.exportAll().collections);
    expect(again.dropped).toBe(0);
    expect(again.drawings).toBe(2);
  });
});
