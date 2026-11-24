/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Unit and property tests for immutable AST edits.
 */

import type {RuleNode, StrategyDefinition} from '@joseph/contracts';
import {describe, expect, it} from 'vitest';
import {
  AstEditError,
  addChild,
  childrenOf,
  ensureIds,
  findNode,
  moveNode,
  removeNode,
  replaceNode,
  setField,
  shiftNode,
  treeStats,
  unwrapNode,
  walk,
  wrapNode,
} from './ast';
import {NODE_CATALOG} from './catalog';
import {STRATEGY_TEMPLATES} from './templates';
import {schemaErrors} from './testing/schema';
import {testVocabulary} from './testing/vocabulary';

/**
 * Returns the EMA trend template with ids.
 * @return Definition.
 * @throws `Error` if the template is missing.
 */
function emaTrend(): StrategyDefinition {
  const def = STRATEGY_TEMPLATES[0]?.create();
  if (!def) throw new Error('missing template');
  return {...def, when: ensureIds(def.when)};
}

/**
 * Collects all node ids.
 * @param root Root node.
 * @return Ids in pre-order, `?` for a node without one.
 */
function ids(root: RuleNode): string[] {
  const out: string[] = [];
  walk(root, node => out.push(node.id ?? '?'));
  return out;
}

describe('AST edits', () => {
  it('assigns unique ids without touching existing ones', () => {
    const short = STRATEGY_TEMPLATES[1]?.create();
    if (!short) throw new Error('missing template');
    const tree = ensureIds(short.when);
    const all = ids(tree);
    expect(new Set(all).size).toBe(all.length);
    expect(ids(ensureIds(emaTrend().when)).slice(0, 3)).toEqual([
      'root',
      'cross',
      'htf',
    ]);
  });

  it('adds, removes and replaces without mutating the input', () => {
    const before = emaTrend().when;
    const snapshot = JSON.stringify(before);
    const added = addChild(before, 'root', {
      op: 'TIME_WINDOW',
      time_zone: 'UTC',
      windows: [{start: '08:00', end: '16:00'}],
    });
    expect(childrenOf(added)).toHaveLength(5);
    expect(JSON.stringify(before)).toBe(snapshot);
    const removed = removeNode(added, 'htf');
    expect(findNode(removed, 'htf')).toBeNull();
    const replaced = replaceNode(removed, 'rr', {
      op: 'RISK_REWARD',
      min_rr: '3',
    });
    expect(findNode(replaced, 'rr')?.node).toMatchObject({
      op: 'RISK_REWARD',
      min_rr: '3',
      id: 'rr',
    });
  });

  it('wraps and unwraps conditions', () => {
    const wrapped = wrapNode(emaTrend().when, 'flow', 'NOT');
    const location = findNode(wrapped, 'flow');
    expect(location?.parent?.op).toBe('NOT');
    const parentId = location?.parent?.id;
    if (!parentId) throw new Error('missing wrapper id');
    expect(findNode(unwrapNode(wrapped, parentId), 'flow')?.parent?.id).toBe(
      'root'
    );
  });

  it('refuses edits that break the tree', () => {
    const tree = emaTrend().when;
    expect(() => removeNode(tree, 'root')).toThrow(AstEditError);
    const single: RuleNode = {
      op: 'ALL',
      id: 'g',
      children: [{op: 'RISK_REWARD', id: 'r', min_rr: '2'}],
    };
    expect(() => removeNode(single, 'r')).toThrow(/at least one condition/);
    expect(() =>
      addChild(tree, 'htf', {op: 'RISK_REWARD', min_rr: '1'})
    ).toThrow(AstEditError);
    expect(() => setField(tree, 'htf', 'op', 'ANY')).toThrow(AstEditError);
    const nested = wrapNode(tree, 'flow', 'ANY');
    const groupId = findNode(nested, 'flow')?.parent?.id ?? '';
    expect(() => moveNode(nested, groupId, groupId, 0)).toThrow(
      /inside itself/
    );
  });

  it('moves conditions between groups and shifts within a group', () => {
    let tree = wrapNode(emaTrend().when, 'flow', 'ANY');
    const groupId = findNode(tree, 'flow')?.parent?.id ?? '';
    tree = moveNode(tree, 'rr', groupId, 0);
    expect(findNode(tree, 'rr')?.parent?.id).toBe(groupId);
    const shifted = shiftNode(tree, 'htf', -1);
    expect(childrenOf(shifted)[0]?.id).toBe('htf');
    expect(shiftNode(shifted, 'htf', -1)).toBe(shifted);
  });

  // Randomized and slow under coverage instrumentation: generous timeout.
  it(
    'keeps the definition schema-valid under random edits (property)',
    {timeout: 60_000},
    () => {
      let seed = 42;
      const rand = (n: number) => {
        seed = (seed * 1103515245 + 12345) % 2147483648;
        return seed % n;
      };
      const vocab = testVocabulary();
      const entries = NODE_CATALOG.filter(entry => entry.available(vocab));
      let def = emaTrend();
      for (let i = 0; i < 300; i += 1) {
        const all: RuleNode[] = [];
        walk(def.when, node => all.push(node));
        const target = all[rand(all.length)];
        const groups = all.filter(n => n.op === 'ALL' || n.op === 'ANY');
        const group = groups[rand(groups.length)];
        if (!target?.id || !group?.id) continue;
        try {
          switch (rand(6)) {
            case 0: {
              const entry = entries[rand(entries.length)];
              if (entry && treeStats(def.when).nodeCount < 40) {
                def = {
                  ...def,
                  when: addChild(
                    def.when,
                    group.id,
                    entry.create(def.direction, def.signal_timeframe, vocab)
                  ),
                };
              }
              break;
            }
            case 1:
              def = {...def, when: removeNode(def.when, target.id)};
              break;
            case 2:
              if (treeStats(def.when).maxDepth < 7) {
                def = {
                  ...def,
                  when: wrapNode(
                    def.when,
                    target.id,
                    (['ALL', 'ANY', 'NOT'] as const)[rand(3)] ?? 'NOT'
                  ),
                };
              }
              break;
            case 3:
              def = {...def, when: unwrapNode(def.when, target.id)};
              break;
            case 4:
              def = {
                ...def,
                when: shiftNode(def.when, target.id, rand(2) ? 1 : -1),
              };
              break;
            default:
              def = {
                ...def,
                when: moveNode(def.when, target.id, group.id, rand(3)),
              };
          }
        } catch (err) {
          expect(err).toBeInstanceOf(AstEditError);
        }
        const all2 = ids(def.when);
        expect(new Set(all2).size).toBe(all2.length);
        expect(
          schemaErrors('strategy.schema.json', 'StrategyDefinition', def)
        ).toEqual([]);
      }
    }
  );
});
