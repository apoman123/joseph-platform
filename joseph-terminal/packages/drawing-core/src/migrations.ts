/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Drawing document migrations.
 *
 * Saved workspaces must stay loadable after upgrades. Documents from older
 * schema versions are migrated step by step; documents from unknown (newer)
 * versions are preserved verbatim as opaque payloads and saved back
 * unchanged, never deleted.
 */

import type {DrawingDocument} from '@joseph/contracts';
import {DRAWING_SCHEMA_VERSION} from './document';

/**
 * Migrates a raw document from one schema version to the next. May throw on
 * input it cannot migrate; the registry then keeps the payload opaque.
 */
export type SchemaMigration = (
  raw: Record<string, unknown>
) => Record<string, unknown>;

/**
 * Result of reading one stored drawing: a current-version document (with
 * whether a migration ran), or the unchanged payload and why it could not
 * be read.
 */
export type MigrationOutcome =
  | {kind: 'document'; doc: DrawingDocument; migrated: boolean}
  | {kind: 'opaque'; raw: unknown; reason: string};

/**
 * Legacy v0 shape: drawings saved before the schema was versioned, with
 * numeric points and no style beyond a color.
 */
interface LegacyV0 {
  /** Drawing id. */
  id?: unknown;
  /** Tool id. */
  tool?: unknown;
  /** Instrument id. */
  instrument?: unknown;
  /** Points: `t` is the time in epoch ms, `p` the price as a number. */
  points?: Array<{t: number; p: number}>;
  /** Hex color. */
  color?: unknown;
}

/**
 * Migrates v0 to v1: numeric `points` become decimal-string anchors with a
 * full style. Missing fields get defaults (a horizontal line on no
 * instrument); `MigrationRegistry.read` validates the result.
 * @param raw Legacy document.
 * @return v1 document.
 */
export const migrateV0ToV1: SchemaMigration = raw => {
  // v0 fields are untyped; each one is checked or coerced below.
  const legacy = raw as LegacyV0;
  const points = Array.isArray(legacy.points) ? legacy.points : [];
  // v0 stored no timestamps: the first point's time stands in for the
  // creation time and names drawings saved without an id.
  const firstTimeMs = points[0]?.t ?? 0;
  return {
    schema_version: 1,
    drawing_id: String(legacy.id ?? `legacy-${firstTimeMs}`),
    tool_id: String(legacy.tool ?? 'horizontal-line'),
    tool_version: 1,
    instrument_id: String(legacy.instrument ?? ''),
    timeframe: null,
    anchors: points.map(point => ({
      time_ms: Math.max(0, Math.round(point.t)),
      price: String(point.p),
    })),
    style: {
      // The theme's default user mark color (`--course` magenta).
      color: typeof legacy.color === 'string' ? legacy.color : '#e66fd2',
      line_width: 1,
      line_style: 'solid',
    },
    text: null,
    props: {},
    locked: false,
    visible: true,
    z_index: 0,
    created_at_ms: firstTimeMs,
    updated_at_ms: firstTimeMs,
  };
};

/** Ordered schema migrations keyed by source version. */
export class MigrationRegistry {
  private readonly steps = new Map<number, SchemaMigration>([
    [0, migrateV0ToV1],
  ]);

  /**
   * Registers a migration from `fromVersion` to `fromVersion + 1`,
   * replacing any step registered for that version.
   * @param fromVersion Source schema version.
   * @param migration Migration step.
   */
  register(fromVersion: number, migration: SchemaMigration): void {
    this.steps.set(fromVersion, migration);
  }

  /**
   * Reads one stored drawing, migrating it to the current version step by
   * step. Never throws: a payload that is not an object, comes from a newer
   * schema, lacks a migration step, makes a step throw or fails the
   * structural check is returned opaque, unchanged.
   * @param raw Stored JSON value (no `schema_version` means v0).
   * @return Document, or an opaque payload with the reason.
   */
  read(raw: unknown): MigrationOutcome {
    if (!raw || typeof raw !== 'object') {
      return {kind: 'opaque', raw, reason: 'Not an object'};
    }
    // Checked to be a non-null object above; its fields are read as unknown.
    let current = raw as Record<string, unknown>;
    let version =
      typeof current.schema_version === 'number' ? current.schema_version : 0;
    if (version > DRAWING_SCHEMA_VERSION) {
      return {
        kind: 'opaque',
        raw,
        reason: `Saved by a newer version (schema ${version})`,
      };
    }
    let migrated = false;
    while (version < DRAWING_SCHEMA_VERSION) {
      const step = this.steps.get(version);
      if (!step) {
        return {
          kind: 'opaque',
          raw,
          reason: `No migration from schema ${version}`,
        };
      }
      try {
        current = step(current);
      } catch {
        return {
          kind: 'opaque',
          raw,
          reason: `Migration from schema ${version} failed`,
        };
      }
      version += 1;
      migrated = true;
    }
    if (!isDrawingDocument(current)) {
      return {kind: 'opaque', raw, reason: 'Missing required fields'};
    }
    return {kind: 'document', doc: current, migrated};
  }
}

/**
 * Checks whether a value is structurally a v1 drawing document.
 * @param value Candidate.
 * @return True when every required field has the right JSON type (values
 *     are not range-checked; see `isImportableDrawing` for that).
 */
export function isDrawingDocument(value: unknown): value is DrawingDocument {
  if (!value || typeof value !== 'object') return false;
  // Partial: each field is type-checked before the guard succeeds.
  const doc = value as Partial<DrawingDocument>;
  return (
    doc.schema_version === 1 &&
    typeof doc.drawing_id === 'string' &&
    typeof doc.tool_id === 'string' &&
    typeof doc.tool_version === 'number' &&
    typeof doc.instrument_id === 'string' &&
    Array.isArray(doc.anchors) &&
    doc.anchors.every(
      anchor =>
        typeof anchor?.time_ms === 'number' && typeof anchor?.price === 'string'
    ) &&
    typeof doc.style === 'object' &&
    doc.style !== null &&
    typeof doc.locked === 'boolean' &&
    typeof doc.visible === 'boolean' &&
    typeof doc.z_index === 'number'
  );
}
