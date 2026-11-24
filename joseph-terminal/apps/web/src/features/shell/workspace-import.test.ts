/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Workspace import with drawings: restored collections reach
 * storage, open charts that keep their instrument reload their drawings
 * at once, and the notification says what was imported.
 */

import type {DrawingDocument} from '@joseph/contracts';
import {DrawingRepository, DrawingStore} from '@joseph/drawing-core';
import {
  createDefaultWorkspace,
  exportWorkspace,
  exportWorkspaceFile,
  parseWorkspaceFile,
  type WorkspaceFileParseResult,
} from '@joseph/terminal-shell';
import {beforeEach, describe, expect, it} from 'vitest';
import {createRuntime, type TerminalRuntime} from '../../composition/runtime';
import {importWorkspaceFile} from './chart-tools-feature';

/** DEMO configuration. */
const DEMO = {
  marketApiUrl: null,
  marketWsUrl: null,
  executionApiUrl: null,
  executionWsUrl: null,
  devToken: null,
  demo: true,
};

const BTC = 'mock:perp:BTCUSD';
const ETH = 'mock:perp:ETHUSD';

/**
 * Builds a two-anchor trendline drawing.
 * @param id Drawing id.
 * @param instrumentId Instrument; the DEMO BTCUSD perpetual by default.
 * @return Document.
 */
function drawing(id: string, instrumentId = BTC): DrawingDocument {
  return {
    schema_version: 1,
    drawing_id: id,
    tool_id: 'trendline',
    tool_version: 1,
    instrument_id: instrumentId,
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
  };
}

/**
 * Parses a file, failing the test when it is refused.
 * @param text File text.
 * @return Parsed file.
 * @throws `Error` with the parser's message when the file is refused.
 */
function parsed(text: string): Extract<WorkspaceFileParseResult, {ok: true}> {
  const result = parseWorkspaceFile(text, 1);
  if (!result.ok) throw new Error(result.error);
  return result;
}

describe('workspace import with drawings', () => {
  let runtime: TerminalRuntime;
  beforeEach(() => {
    localStorage.clear();
    runtime = createRuntime(() => [], DEMO);
  });

  it('restores drawings and reloads open charts on the same instrument', () => {
    const current = runtime.workspace.get();
    const repository = new DrawingRepository(
      runtime.storage,
      current.workspace_id
    );
    repository.save(BTC, {drawings: [drawing('old')], opaque: []});
    const open = new DrawingStore(repository.load(BTC));
    runtime.drawing.stores.set('chart-1', open);
    runtime.drawing.selection.set({chartId: 'chart-1', drawingId: 'old'});
    const revision = runtime.drawing.revision.get();
    const file = exportWorkspaceFile(
      {...current, workspace_id: 'elsewhere'},
      [
        {instrument_id: BTC, drawings: [drawing('a'), drawing('b')]},
        {
          instrument_id: ETH,
          drawings: [drawing('e', ETH), drawing('bad', BTC)],
          opaque: [{schema_version: 9, drawing_id: 'future'}],
        },
      ],
      1
    );
    const message = importWorkspaceFile(runtime, parsed(file));
    expect(message).toBe(
      'Imported the workspace and 3 drawings on 2 instruments; they replace the drawings saved for those instruments. 1 drawing from a newer version was kept as unsupported. 1 unreadable entry was skipped.'
    );
    expect(runtime.workspace.get().workspace_id).toBe(current.workspace_id);
    expect(open.get().drawings.map(d => d.drawing_id)).toEqual(['a', 'b']);
    expect(runtime.drawing.selection.get()).toBeNull();
    expect(runtime.drawing.revision.get()).toBeGreaterThan(revision);
    expect(repository.load(ETH).drawings.map(d => d.drawing_id)).toEqual(['e']);
    expect(repository.load(ETH).opaque).toEqual([
      {schema_version: 9, drawing_id: 'future'},
    ]);
  });

  it('leaves charts that switch instrument to load the restored drawings', () => {
    const current = runtime.workspace.get();
    const open = new DrawingStore({drawings: [drawing('keep')], opaque: []});
    runtime.drawing.stores.set('chart-1', open);
    const imported = createDefaultWorkspace({
      workspaceId: 'other',
      instrumentId: ETH,
      timeframe: '1h',
      nowMs: 1,
    });
    const file = exportWorkspaceFile(
      imported,
      [{instrument_id: ETH, drawings: [drawing('e', ETH)]}],
      1
    );
    const message = importWorkspaceFile(runtime, parsed(file));
    expect(message).toBe(
      'Imported the workspace and 1 drawing on 1 instrument; they replace the drawings saved for that instrument.'
    );
    // The pane reloads when its instrument changes; this store is untouched.
    expect(open.get().drawings.map(d => d.drawing_id)).toEqual(['keep']);
    expect(runtime.workspace.get().charts[0]?.instrument_id).toBe(ETH);
    const repository = new DrawingRepository(
      runtime.storage,
      current.workspace_id
    );
    expect(repository.load(ETH).drawings).toHaveLength(1);
  });

  it('keeps drawings untouched for files without drawings', () => {
    const repository = new DrawingRepository(
      runtime.storage,
      runtime.workspace.get().workspace_id
    );
    repository.save(BTC, {drawings: [drawing('mine')], opaque: []});
    expect(
      importWorkspaceFile(
        runtime,
        parsed(exportWorkspace(runtime.workspace.get()))
      )
    ).toBe(
      'Imported the workspace. The file has no drawings, so your drawings were not changed.'
    );
    expect(
      importWorkspaceFile(
        runtime,
        parsed(JSON.stringify({exchange: 'mock', symbol: 'solusd'}))
      )
    ).toMatch(/upgraded from an older format\)\. The file has no drawings/);
    expect(repository.load(BTC).drawings).toHaveLength(1);
  });
});
