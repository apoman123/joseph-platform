/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Manifest helper for the shell-owned features (chart tools,
 * drawing, indicators, market info, data health, workspace, theme and
 * palette).
 */

import type {FeatureManifest} from '@joseph/feature-registry';
import {CONTRACT_VERSION} from '../../composition/runtime';

/**
 * Builds a shell feature manifest: version 1.0.0, owned by
 * `terminal-shell`, settings schema 1, the runtime's contract version and
 * no slots unless `extra` sets them.
 * @param id Feature id.
 * @param displayName Name shown in the UI.
 * @param description One-line purpose.
 * @param extra Fields that override the defaults (slots, capabilities,
 *     permissions, ...).
 * @return Manifest.
 */
export function shellManifest(
  id: string,
  displayName: string,
  description: string,
  extra: Partial<FeatureManifest> = {}
): FeatureManifest {
  return {
    id,
    version: '1.0.0',
    owner: 'terminal-shell',
    displayName,
    description,
    uiSlots: [],
    settingsSchemaVersion: 1,
    contractVersion: CONTRACT_VERSION,
    removal: {notes: 'No persisted data beyond workspace preferences.'},
    ...extra,
  };
}
