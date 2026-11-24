/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Workspace export files: the format 2 envelope with
 * drawings, the format 0 → 1 → 2 migration path, size limits and the
 * actionable errors for files the terminal can't import.
 */

import {readFileSync, readdirSync} from 'node:fs';
import {join} from 'node:path';
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import type {WorkspaceExportDrawings} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  MAX_EXPORT_COLLECTIONS,
  MAX_WORKSPACE_FILE_BYTES,
  WORKSPACE_EXPORT_FORMAT,
  createWorkspaceExport,
  exportWorkspace,
  exportWorkspaceFile,
  parseWorkspace,
  parseWorkspaceFile,
} from './persistence';
import {createDefaultWorkspace} from './workspace';

const SCHEMA_DIR = join(__dirname, '..', '..', 'contracts', 'schemas');

const BASE = createDefaultWorkspace({
  workspaceId: 'ws',
  instrumentId: 'mock:perp:BTCUSD',
  timeframe: '5m',
  nowMs: 1,
});

const DRAWINGS: WorkspaceExportDrawings[] = [
  {
    instrument_id: 'mock:perp:BTCUSD',
    drawings: [
      {
        schema_version: 1,
        drawing_id: 'd1',
        tool_id: 'trendline',
        tool_version: 1,
        instrument_id: 'mock:perp:BTCUSD',
        timeframe: null,
        anchors: [
          {time_ms: 1_000, price: '95000'},
          {time_ms: 2_000, price: '96000'},
        ],
        style: {color: '#e66fd2', line_width: 1, line_style: 'solid'},
        text: null,
        props: {},
        locked: false,
        visible: true,
        z_index: 0,
        created_at_ms: 1,
        updated_at_ms: 1,
      },
    ],
    opaque: [{schema_version: 4, drawing_id: 'from-the-future'}],
  },
];

/**
 * Validates a value against a contract definition.
 * @param definition Definition name in workspace.schema.json.
 * @param value Candidate.
 * @return Validation errors (empty when valid).
 */
function contractErrors(definition: string, value: unknown): unknown[] {
  const ajv = new Ajv2020({strict: false, allErrors: true});
  addFormats(ajv);
  for (const file of readdirSync(SCHEMA_DIR)) {
    if (file.endsWith('.schema.json')) {
      ajv.addSchema(JSON.parse(readFileSync(join(SCHEMA_DIR, file), 'utf8')));
    }
  }
  const validate = ajv.getSchema(
    `https://schemas.joseph.local/v1/workspace.schema.json#/$defs/${definition}`
  );
  if (!validate) throw new Error(`missing ${definition}`);
  return validate(value) ? [] : (validate.errors ?? ['invalid']);
}

describe('workspace export files', () => {
  it('writes a contract-valid format 2 envelope that round-trips', () => {
    const text = exportWorkspaceFile(BASE, DRAWINGS, 1_800_000_000_000.4);
    const envelope = JSON.parse(text) as unknown;
    expect(contractErrors('WorkspaceExport', envelope)).toEqual([]);
    expect(envelope).toMatchObject({
      format: WORKSPACE_EXPORT_FORMAT,
      schema_version: 2,
      exported_at_ms: 1_800_000_000_000,
    });
    const parsed = parseWorkspaceFile(text, 5);
    expect(parsed).toEqual({
      ok: true,
      workspace: BASE,
      migrated: false,
      formatVersion: 2,
      drawings: DRAWINGS,
    });
  });

  it('imports plain v1 documents without touching drawings', () => {
    const parsed = parseWorkspaceFile(exportWorkspace(BASE), 5);
    expect(parsed).toMatchObject({
      ok: true,
      formatVersion: 1,
      migrated: false,
      drawings: null,
    });
  });

  it('migrates the V4 starter shape (format 0)', () => {
    const parsed = parseWorkspaceFile(
      JSON.stringify({exchange: 'mock', symbol: 'ethusd', timeframe: '1h'}),
      7
    );
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.formatVersion).toBe(0);
    expect(parsed.migrated).toBe(true);
    expect(parsed.drawings).toBeNull();
    expect(parsed.workspace.charts[0]?.instrument_id).toBe('mock:perp:ETHUSD');
  });

  it('accepts files with drawings beyond the 64 KB document limit', () => {
    const heavy = [
      {
        ...DRAWINGS[0],
        drawings: Array.from({length: 300}, (_, i) => ({
          ...DRAWINGS[0]?.drawings[0],
          drawing_id: `d${i}`,
        })),
      },
    ] as WorkspaceExportDrawings[];
    const text = exportWorkspaceFile(BASE, heavy, 1);
    expect(text.length).toBeGreaterThan(64 * 1024);
    expect(parseWorkspaceFile(text, 1).ok).toBe(true);
    // The document parser (storage, share links) keeps its 64 KB limit.
    expect(parseWorkspace(text, 1).ok).toBe(false);
  });

  it('refuses oversize, malformed and unknown files with a reason', () => {
    const cases: Array<[string, RegExp]> = [
      [' '.repeat(MAX_WORKSPACE_FILE_BYTES + 1), /larger than 2 MB/],
      ['{nope', /not valid JSON/],
      ['[1, 2]', /must be an object/],
      ['42', /must be an object/],
      [JSON.stringify({format: 'something-else'}), /not a Joseph workspace/],
      [
        JSON.stringify({format: WORKSPACE_EXPORT_FORMAT, schema_version: 3}),
        /newer Joseph version \(export format 3\)/,
      ],
      [
        JSON.stringify({format: WORKSPACE_EXPORT_FORMAT, schema_version: 1}),
        /Unsupported workspace export version/,
      ],
      [
        JSON.stringify({
          ...createWorkspaceExport(BASE, [], 1),
          workspace: {...BASE, charts: []},
        }),
        /1 to 8 charts/,
      ],
      [
        JSON.stringify({...createWorkspaceExport(BASE, [], 1), drawings: {}}),
        /drawings in the file are malformed/,
      ],
      [
        JSON.stringify({
          ...createWorkspaceExport(BASE, [], 1),
          drawings: Array.from(
            {length: MAX_EXPORT_COLLECTIONS + 1},
            () => ({})
          ),
        }),
        /more than 500 instruments/,
      ],
      [
        JSON.stringify({
          ...createWorkspaceExport(BASE, [], 1),
          workspace: {...BASE, panels: {big: 'x'.repeat(70_000)}},
        }),
        /layout is larger than 64 KB/,
      ],
      [JSON.stringify({...BASE, schema_version: 2}), /Unsupported workspace/],
    ];
    for (const [text, error] of cases) {
      const parsed = parseWorkspaceFile(text, 1);
      expect(parsed.ok).toBe(false);
      if (!parsed.ok) expect(parsed.error).toMatch(error);
    }
  });

  it('treats a missing drawings list as empty and caps collections', () => {
    const {drawings: _drawings, ...bare} = createWorkspaceExport(BASE, [], 1);
    const parsed = parseWorkspaceFile(JSON.stringify(bare), 1);
    expect(parsed).toMatchObject({ok: true, formatVersion: 2, drawings: []});
    const many = Array.from(
      {length: MAX_EXPORT_COLLECTIONS + 10},
      () => DRAWINGS[0] as WorkspaceExportDrawings
    );
    expect(createWorkspaceExport(BASE, many, 1).drawings).toHaveLength(
      MAX_EXPORT_COLLECTIONS
    );
  });
});
