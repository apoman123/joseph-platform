/**
 * @fileoverview JSON import/export and version diffing for strategies.
 */
import type {StrategyDefinition} from '@joseph/contracts';
import {ensureIds} from './ast';
import {
  isStrategyConfigV1,
  migrateV1,
  type MigrationReport,
} from './migrate-v1';
import {validateDefinition, type ClientValidation} from './validate';

/** Successful import. */
export interface ImportSuccess {
  /** Discriminator. */
  ok: true;
  /** Imported (and possibly migrated) definition with node ids assigned. */
  definition: StrategyDefinition;
  /** Validation of the imported definition. */
  validation: ClientValidation;
  /** Present when a v1 config was migrated. */
  migration: MigrationReport | null;
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
 * @param definition Definition.
 * @return Pretty-printed JSON.
 */
export function exportDefinition(definition: StrategyDefinition): string {
  return `${JSON.stringify(definition, null, 2)}\n`;
}

/**
 * Parses strategy JSON (v2 definition or starter v1 config).
 * @param text JSON text.
 * @return Import result.
 */
export function importDefinition(text: string): ImportSuccess | ImportFailure {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return {ok: false, error: 'The file is not valid JSON.'};
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return {ok: false, error: 'Expected a strategy object.'};
  }
  if (isStrategyConfigV1(parsed)) {
    const {definition, report} = migrateV1(parsed);
    const withIds = {...definition, when: ensureIds(definition.when)};
    return {
      ok: true,
      definition: withIds,
      validation: validateDefinition(withIds),
      migration: report,
    };
  }
  const version = (parsed as {schema_version?: unknown}).schema_version;
  if (version !== 2) {
    return {
      ok: false,
      error: `Unsupported rule schema version ${String(version)}. This editor reads version 2 rules and starter v1 configs.`,
    };
  }
  const validation = validateDefinition(parsed);
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
      validation: validateDefinition(withIds),
      migration: null,
    };
  }
  return {ok: true, definition, validation, migration: null};
}

/** One difference between two definitions. */
export interface DefinitionChange {
  /** JSON pointer of the changed value. */
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
 * @param before Older definition.
 * @param after Newer definition.
 * @return Changes in document order.
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
        if (i >= a.length)
          changes.push({path: `${path}/${i}`, kind: 'added', after: b[i]});
        else if (i >= b.length)
          changes.push({path: `${path}/${i}`, kind: 'removed', before: a[i]});
        else step(a[i], b[i], `${path}/${i}`);
      }
      return;
    }
    if (isObject(a) && isObject(b)) {
      const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])];
      for (const key of keys) {
        const child = `${path}/${key}`;
        if (!(key in a))
          changes.push({path: child, kind: 'added', after: b[key]});
        else if (!(key in b))
          changes.push({path: child, kind: 'removed', before: a[key]});
        else step(a[key], b[key], child);
      }
      return;
    }
    if (a !== b) changes.push({path, kind: 'changed', before: a, after: b});
  };
  step(before, after, '');
  return changes;
}
