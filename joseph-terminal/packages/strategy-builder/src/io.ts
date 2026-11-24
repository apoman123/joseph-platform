/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview JSON import/export and version diffing for strategies.
 */

import type {StrategyDefinition} from '@joseph/contracts';
import {ensureIds} from './ast';
import {validateDefinition, type ClientValidation} from './validate';
import type {Vocabulary} from './vocabulary';

/** Successful import. */
export interface ImportSuccess {
  /** Discriminator. */
  ok: true;
  /**
   * Imported definition; node ids are assigned whenever `when` is a rule
   * node.
   */
  definition: StrategyDefinition;
  /** Client validation of `definition`; errors do not fail the import. */
  validation: ClientValidation;
}

/** Failed import. */
export interface ImportFailure {
  /** Discriminator. */
  ok: false;
  /** Plain-language reason. */
  error: string;
}

/**
 * Serializes a definition for export.
 * @param definition Definition to export.
 * @return JSON indented by two spaces, with a trailing newline.
 */
export function exportDefinition(definition: StrategyDefinition): string {
  return `${JSON.stringify(definition, null, 2)}\n`;
}

/**
 * Parses a Rule AST v2 strategy definition.
 *
 * A definition is accepted even when it has validation errors, so the
 * editor can open it and show them.
 * @param text JSON text.
 * @param vocab Vocabulary of the installed plugins; without it only the
 *     structure is validated.
 * @return Success with the definition and its validation; failure for
 *     invalid JSON, a non-object, or a schema version other than 2.
 */
export function importDefinition(
  text: string,
  vocab?: Vocabulary
): ImportSuccess | ImportFailure {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return {ok: false, error: 'The file is not valid JSON.'};
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return {ok: false, error: 'Expected a strategy object.'};
  }
  if ((parsed as {schema_version?: unknown}).schema_version !== 2) {
    return {
      ok: false,
      error: 'Expected a Rule AST v2 definition (schema_version 2).',
    };
  }
  // Unchecked on purpose: the validation lists whatever does not match the
  // type, and the editor shows those errors instead of refusing the file.
  const definition = parsed as StrategyDefinition;
  if (
    typeof definition.when === 'object' &&
    definition.when !== null &&
    'op' in definition.when
  ) {
    const withIds = {...definition, when: ensureIds(definition.when)};
    return {
      ok: true,
      definition: withIds,
      validation: validateDefinition(withIds, vocab),
    };
  }
  return {ok: true, definition, validation: validateDefinition(parsed, vocab)};
}

/** One difference between two definitions. */
export interface DefinitionChange {
  /** JSON-pointer-style path of the changed value (keys are not escaped). */
  path: string;
  /** Kind of change. */
  kind: 'added' | 'removed' | 'changed';
  /** Previous value (absent for additions). */
  before?: unknown;
  /** New value (absent for removals). */
  after?: unknown;
}

/**
 * Lists the differences between two definitions (e.g. two versions).
 *
 * Objects are compared key by key and arrays index by index; any other
 * difference, including a type change, is one `changed` entry.
 * @param before Older definition (any JSON value).
 * @param after Newer definition (any JSON value).
 * @return Changes in document order: `before`'s keys first, then keys only
 *     `after` has; empty when the values are equal.
 */
export function diffDefinitions(
  before: unknown,
  after: unknown
): DefinitionChange[] {
  const changes: DefinitionChange[] = [];
  const isObject = (v: unknown): v is Record<string, unknown> =>
    typeof v === 'object' && v !== null && !Array.isArray(v);
  const step = (a: unknown, b: unknown, path: string) => {
    if (Array.isArray(a) && Array.isArray(b)) {
      const length = Math.max(a.length, b.length);
      for (let i = 0; i < length; i += 1) {
        if (i >= a.length) {
          changes.push({path: `${path}/${i}`, kind: 'added', after: b[i]});
        } else if (i >= b.length) {
          changes.push({path: `${path}/${i}`, kind: 'removed', before: a[i]});
        } else step(a[i], b[i], `${path}/${i}`);
      }
      return;
    }
    if (isObject(a) && isObject(b)) {
      const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])];
      for (const key of keys) {
        const child = `${path}/${key}`;
        if (!(key in a)) {
          changes.push({path: child, kind: 'added', after: b[key]});
        } else if (!(key in b)) {
          changes.push({path: child, kind: 'removed', before: a[key]});
        } else step(a[key], b[key], child);
      }
      return;
    }
    if (a !== b) changes.push({path, kind: 'changed', before: a, after: b});
  };
  step(before, after, '');
  return changes;
}
