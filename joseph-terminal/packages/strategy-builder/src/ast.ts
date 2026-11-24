/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Immutable editing operations on the Rule AST.
 *
 * Edits address nodes by their stable `id` (see `ensureIds`), so the editor,
 * the server's reason tree and validation messages can refer to the same
 * node across edits. All operations return a new tree and never mutate their
 * input.
 */

import type {RuleNode} from '@joseph/contracts';

/** Group nodes (ALL, ANY) with a `children` array. */
export type GroupNode = Extract<RuleNode, {op: 'ALL' | 'ANY'}>;

/** Wrapper nodes with a single `child`. */
export type WrapperNode = Extract<
  RuleNode,
  {op: 'NOT' | 'PERSIST_FOR' | 'WITHIN_BARS'}
>;

/** Error thrown for an edit that would produce an invalid tree. */
export class AstEditError extends Error {
  /**
   * Creates the error.
   * @param message Plain-language explanation.
   */
  constructor(message: string) {
    super(message);
    this.name = 'AstEditError';
  }
}

/**
 * Returns whether a node is an ALL/ANY group.
 * @param node Node to test.
 * @return True for ALL and ANY.
 */
export function isGroup(node: RuleNode): node is GroupNode {
  return node.op === 'ALL' || node.op === 'ANY';
}

/**
 * Returns whether a node wraps a single child.
 * @param node Node to test.
 * @return True for NOT, PERSIST_FOR and WITHIN_BARS.
 */
export function isWrapper(node: RuleNode): node is WrapperNode {
  return (
    node.op === 'NOT' || node.op === 'PERSIST_FOR' || node.op === 'WITHIN_BARS'
  );
}

/**
 * Lists a node's direct children.
 * @param node Node to inspect.
 * @return Children in order (empty for leaves).
 */
export function childrenOf(node: RuleNode): readonly RuleNode[] {
  if (isGroup(node)) return node.children;
  if (isWrapper(node)) return [node.child];
  return [];
}

/**
 * Returns a copy of a node with new children.
 * @param node Group or wrapper node.
 * @param children Replacement children.
 * @return Updated node.
 * @throws `AstEditError` when a group would be left empty, a wrapper would
 *     not get exactly one child, or `node` is a leaf.
 */
function withChildren(node: RuleNode, children: readonly RuleNode[]): RuleNode {
  if (isGroup(node)) {
    if (children.length === 0) {
      throw new AstEditError(
        'A group needs at least one condition. Remove the group instead.'
      );
    }
    return {...node, children: [...children] as [RuleNode, ...RuleNode[]]};
  }
  if (isWrapper(node)) {
    const [child] = children;
    if (!child || children.length !== 1) {
      throw new AstEditError('This wrapper holds exactly one condition.');
    }
    return {...node, child};
  }
  throw new AstEditError('Only groups and wrappers have child conditions.');
}

/**
 * Visits every node depth-first (pre-order).
 * @param root Root node.
 * @param visit Callback receiving the node, its depth (1 for the root) and
 *     its JSON-pointer-style path relative to the root (`''` for the root).
 */
export function walk(
  root: RuleNode,
  visit: (node: RuleNode, depth: number, path: string) => void
): void {
  const step = (node: RuleNode, depth: number, path: string) => {
    visit(node, depth, path);
    if (isGroup(node)) {
      node.children.forEach(
        (child, i) => void step(child, depth + 1, `${path}/children/${i}`)
      );
    } else if (isWrapper(node)) {
      step(node.child, depth + 1, `${path}/child`);
    }
  };
  step(root, 1, '');
}

/** Location of a node in a tree. */
export interface NodeLocation {
  /** The node. */
  node: RuleNode;
  /** Parent node, or null for the root. */
  parent: RuleNode | null;
  /** Index among the parent's children (0 for the root). */
  index: number;
  /** JSON-pointer path relative to the root (`''` for the root). */
  path: string;
}

/**
 * Finds a node by id.
 * @param root Root node.
 * @param id Node id.
 * @return Location, or null when absent.
 */
export function findNode(root: RuleNode, id: string): NodeLocation | null {
  let found: NodeLocation | null = null;
  const step = (
    node: RuleNode,
    parent: RuleNode | null,
    index: number,
    path: string
  ) => {
    if (found) return;
    if (node.id === id) {
      found = {node, parent, index, path};
      return;
    }
    childrenOf(node).forEach(
      (child, i) =>
        void step(
          child,
          node,
          i,
          isGroup(node) ? `${path}/children/${i}` : `${path}/child`
        )
    );
  };
  step(root, null, 0, '');
  return found;
}

/**
 * Replaces the node with `id` using `update`.
 *
 * Ancestors are copied only along the changed path; untouched subtrees keep
 * their identity.
 * @param root Root node.
 * @param id Node id.
 * @param update Produces the replacement; its `AstEditError`s propagate.
 * @return New root.
 * @throws `AstEditError` when no node has the id.
 */
function updateNode(
  root: RuleNode,
  id: string,
  update: (node: RuleNode) => RuleNode
): RuleNode {
  let hit = false;
  const step = (node: RuleNode): RuleNode => {
    if (node.id === id) {
      hit = true;
      return update(node);
    }
    const children = childrenOf(node);
    if (children.length === 0) return node;
    const next = children.map(step);
    return next.every((child, i) => child === children[i])
      ? node
      : withChildren(node, next);
  };
  const result = step(root);
  if (!hit) throw new AstEditError(`No condition with id ${id}.`);
  return result;
}

/**
 * Returns the next free node id (`n1`, `n2`, ...).
 * @param root Root node.
 * @return `n<k>`, where k is one more than the highest `n<number>` id in the
 *     tree.
 */
export function nextNodeId(root: RuleNode): string {
  let max = 0;
  walk(root, node => {
    const match = /^n(\d+)$/.exec(node.id ?? '');
    if (match) max = Math.max(max, Number(match[1]));
  });
  return `n${max + 1}`;
}

/**
 * Assigns ids to every node that lacks one, keeping existing ids.
 *
 * The first occurrence of a duplicated id keeps it; later ones get a fresh
 * `n<k>` id.
 * @param root Root node.
 * @return Tree where every node has a unique id.
 */
export function ensureIds(root: RuleNode): RuleNode {
  const used = new Set<string>();
  walk(root, node => {
    if (node.id) used.add(node.id);
  });
  let counter = 0;
  const fresh = () => {
    do {
      counter += 1;
    } while (used.has(`n${counter}`));
    used.add(`n${counter}`);
    return `n${counter}`;
  };
  const seen = new Set<string>();
  const step = (node: RuleNode): RuleNode => {
    let id = node.id;
    if (!id || seen.has(id)) id = fresh();
    seen.add(id);
    const base: RuleNode = {...node, id};
    const children = childrenOf(base);
    return children.length ? withChildren(base, children.map(step)) : base;
  };
  return step(root);
}

/**
 * Gives a new subtree ids that do not collide with `root`.
 * @param root Existing tree.
 * @param node Subtree to insert.
 * @return Subtree whose missing or colliding ids are replaced by fresh
 *     `n<k>` ids; other ids are kept.
 */
function freshIds(root: RuleNode, node: RuleNode): RuleNode {
  const used = new Set<string>();
  walk(root, n => {
    if (n.id) used.add(n.id);
  });
  let counter = Number(nextNodeId(root).slice(1)) - 1;
  const step = (n: RuleNode): RuleNode => {
    let id = n.id;
    if (!id || used.has(id)) {
      do {
        counter += 1;
      } while (used.has(`n${counter}`));
      id = `n${counter}`;
    }
    used.add(id);
    const base: RuleNode = {...n, id};
    const children = childrenOf(base);
    return children.length ? withChildren(base, children.map(step)) : base;
  };
  return step(node);
}

/**
 * Adds a condition to an ALL/ANY group.
 * @param root Root node.
 * @param parentId Group id.
 * @param child New condition (ids are assigned if missing or colliding).
 * @param index Insertion index; appends when omitted.
 * @return New root.
 * @throws `AstEditError` when no node has `parentId`, the parent is not a
 *     group, or the group already holds 32 conditions.
 */
export function addChild(
  root: RuleNode,
  parentId: string,
  child: RuleNode,
  index?: number
): RuleNode {
  const prepared = freshIds(root, child);
  return updateNode(root, parentId, parent => {
    if (!isGroup(parent)) {
      throw new AstEditError(
        'Conditions can only be added to an "all" or "any" group.'
      );
    }
    if (parent.children.length >= 32) {
      throw new AstEditError('A group holds at most 32 conditions.');
    }
    const children = [...parent.children];
    children.splice(index ?? children.length, 0, prepared);
    return withChildren(parent, children);
  });
}

/**
 * Removes a condition.
 * @param root Root node.
 * @param id Node id.
 * @return New root.
 * @throws `AstEditError` for an unknown id, the root, a child whose parent
 *     has no id (call `ensureIds` first), a wrapper's only child or a
 *     group's last condition.
 */
export function removeNode(root: RuleNode, id: string): RuleNode {
  const location = findNode(root, id);
  if (!location) throw new AstEditError(`No condition with id ${id}.`);
  const {parent} = location;
  if (!parent) {
    throw new AstEditError('The top-level group cannot be removed.');
  }
  if (parent.id === undefined) {
    throw new AstEditError('Assign node ids with ensureIds before editing.');
  }
  if (!isGroup(parent)) {
    throw new AstEditError('Remove or unwrap the surrounding wrapper instead.');
  }
  if (parent.children.length === 1) {
    throw new AstEditError(
      'A group needs at least one condition. Remove the group instead.'
    );
  }
  return updateNode(root, parent.id, p =>
    withChildren(
      p,
      childrenOf(p).filter(child => child.id !== id)
    )
  );
}

/**
 * Replaces a condition, keeping its id.
 * @param root Root node.
 * @param id Node id.
 * @param node Replacement; its own id is ignored, and missing or colliding
 *     ids in its subtree are replaced by fresh ones.
 * @return New root.
 * @throws `AstEditError` when no node has the id.
 */
export function replaceNode(
  root: RuleNode,
  id: string,
  node: RuleNode
): RuleNode {
  const replacement = freshIds(root, {...node, id: undefined});
  return updateNode(root, id, () => ({...replacement, id}));
}

/**
 * Sets (or clears with `undefined`) one field of a node.
 *
 * The value is not checked; run `validateDefinition` on the result.
 * @param root Root node.
 * @param id Node id.
 * @param field Field name.
 * @param value New value; `undefined` deletes the field.
 * @return New root.
 * @throws `AstEditError` when changing `op`, `id`, `children` or `child`,
 *     or when no node has the id.
 */
export function setField(
  root: RuleNode,
  id: string,
  field: string,
  value: unknown
): RuleNode {
  if (['op', 'id', 'children', 'child'].includes(field)) {
    throw new AstEditError(
      `Field ${field} is structural; use the tree operations instead.`
    );
  }
  return updateNode(root, id, node => {
    const copy: Record<string, unknown> = {...node};
    if (value === undefined) delete copy[field];
    else copy[field] = value;
    // Unchecked by design: callers edit one wire field at a time and
    // validation reports any resulting schema error.
    return copy as unknown as RuleNode;
  });
}

/**
 * Wraps a condition in a new ALL, ANY or NOT node.
 * @param root Root node.
 * @param id Node id.
 * @param op Wrapper kind.
 * @return New root; the wrapper gets the id from `nextNodeId`.
 * @throws `AstEditError` when no node has the id.
 */
export function wrapNode(
  root: RuleNode,
  id: string,
  op: 'ALL' | 'ANY' | 'NOT'
): RuleNode {
  const wrapperId = nextNodeId(root);
  return updateNode(root, id, node =>
    op === 'NOT'
      ? {op: 'NOT', id: wrapperId, child: node}
      : {op, id: wrapperId, children: [node]}
  );
}

/**
 * Replaces a single-child group or wrapper with its child.
 * @param root Root node.
 * @param id Node id.
 * @return New root (the child itself when `id` is the root).
 * @throws `AstEditError` for an unknown id, or when the node has more than
 *     one child or none.
 */
export function unwrapNode(root: RuleNode, id: string): RuleNode {
  const location = findNode(root, id);
  if (!location) throw new AstEditError(`No condition with id ${id}.`);
  const children = childrenOf(location.node);
  const [only] = children;
  if (!only || children.length !== 1) {
    throw new AstEditError(
      'Only a group with exactly one condition, or a wrapper, can be unwrapped.'
    );
  }
  if (location.parent === null) return only;
  return updateNode(root, id, () => only);
}

/**
 * Moves a condition into a group at an index.
 * @param root Root node.
 * @param id Node id to move.
 * @param targetParentId Destination group id.
 * @param index Destination index, clamped to the group's bounds.
 * @return New root.
 * @throws `AstEditError` for an unknown `id` or `targetParentId`, a move
 *     into the node's own subtree or into a non-group, and for the removals
 *     `removeNode` refuses (for example a group's last condition).
 */
export function moveNode(
  root: RuleNode,
  id: string,
  targetParentId: string,
  index: number
): RuleNode {
  const location = findNode(root, id);
  if (!location) throw new AstEditError(`No condition with id ${id}.`);
  if (findNode(location.node, targetParentId)) {
    throw new AstEditError('A condition cannot be moved inside itself.');
  }
  if (location.parent?.id === targetParentId) {
    return updateNode(root, targetParentId, parent => {
      const children = childrenOf(parent).filter(child => child.id !== id);
      children.splice(
        Math.max(0, Math.min(index, children.length)),
        0,
        location.node
      );
      return withChildren(parent, children);
    });
  }
  const without = removeNode(root, id);
  return updateNode(without, targetParentId, parent => {
    if (!isGroup(parent)) {
      throw new AstEditError('Conditions can only be moved into a group.');
    }
    const children = [...parent.children];
    children.splice(
      Math.max(0, Math.min(index, children.length)),
      0,
      location.node
    );
    return withChildren(parent, children);
  });
}

/**
 * Moves a condition up or down within its group.
 * @param root Root node.
 * @param id Node id.
 * @param delta `-1` moves the node up, `+1` moves it down.
 * @return New root; `root` itself when the node is first (up) or last
 *     (down), unknown, or not inside a group with an id.
 */
export function shiftNode(root: RuleNode, id: string, delta: -1 | 1): RuleNode {
  const location = findNode(root, id);
  if (
    !location?.parent ||
    !isGroup(location.parent) ||
    location.parent.id === undefined
  ) {
    return root;
  }
  const target = location.index + delta;
  if (target < 0 || target >= location.parent.children.length) return root;
  return moveNode(root, id, location.parent.id, target);
}

/**
 * Counts nodes and the maximum depth.
 * @param root Root node.
 * @return Node count and depth (root depth is 1).
 */
export function treeStats(root: RuleNode): {
  nodeCount: number;
  maxDepth: number;
} {
  let nodeCount = 0;
  let maxDepth = 0;
  walk(root, (_node, depth) => {
    nodeCount += 1;
    maxDepth = Math.max(maxDepth, depth);
  });
  return {nodeCount, maxDepth};
}
