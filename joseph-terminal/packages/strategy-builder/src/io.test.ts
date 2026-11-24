/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview JSON import/export and version diffs.
 */

import {describe, expect, it} from 'vitest';
import {diffDefinitions, exportDefinition, importDefinition} from './io';
import {STRATEGY_TEMPLATES} from './templates';
import {testVocabulary} from './testing/vocabulary';

describe('import / export', () => {
  it('round-trips a v2 definition', () => {
    const def = STRATEGY_TEMPLATES[0]?.create();
    if (!def) throw new Error('missing template');
    const result = importDefinition(exportDefinition(def), testVocabulary());
    expect(result).toMatchObject({ok: true, definition: def});
    expect(result.ok && result.validation.valid).toBe(true);
  });

  it('assigns node ids and keeps invalid rules editable', () => {
    const def = STRATEGY_TEMPLATES[1]?.create();
    if (!def) throw new Error('missing template');
    const result = importDefinition(
      JSON.stringify({...def, strategy_id: 'Bad Id!'})
    );
    if (!result.ok) throw new Error(result.error);
    expect(result.definition.when.id).toBe('n1');
    expect(result.validation.errors.map(e => e.path)).toEqual(['/strategy_id']);
  });

  it('rejects other schema versions, non-objects and bad JSON', () => {
    const v2Only = {
      ok: false,
      error: 'Expected a Rule AST v2 definition (schema_version 2).',
    };
    expect(importDefinition('{"schema_version": 3}')).toEqual(v2Only);
    expect(importDefinition('{"version": 1, "id": "old"}')).toEqual(v2Only);
    expect(importDefinition('[]')).toMatchObject({ok: false});
    expect(importDefinition('not json')).toEqual({
      ok: false,
      error: 'The file is not valid JSON.',
    });
  });

  it('diffs two versions', () => {
    const a = STRATEGY_TEMPLATES[0]?.create();
    const b = STRATEGY_TEMPLATES[0]?.create();
    if (!a || !b) throw new Error('missing template');
    b.name = 'EMA trend v2';
    b.notification.cooldown_bars = 6;
    delete b.setup;
    expect(diffDefinitions(a, b)).toEqual([
      {path: '/name', kind: 'changed', before: a.name, after: 'EMA trend v2'},
      {
        path: '/notification/cooldown_bars',
        kind: 'changed',
        before: 12,
        after: 6,
      },
      {path: '/setup', kind: 'removed', before: {expire_after_bars: 24}},
    ]);
  });
});
