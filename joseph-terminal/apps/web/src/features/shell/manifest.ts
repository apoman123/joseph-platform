/**
 * @fileoverview Manifest helper for shell-owned features.
 */
import type {FeatureManifest} from '@joseph/feature-registry';
import {CONTRACT_VERSION} from '../../composition/runtime';

/**
 * Builds a shell feature manifest.
 * @param id Feature id.
 * @param displayName Name.
 * @param description Purpose.
 * @param extra Overrides.
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
