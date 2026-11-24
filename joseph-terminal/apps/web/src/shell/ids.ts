/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Client-side identifiers for UI documents (drawings).
 */

/** Id generator namespace. */
export const ids = {
  /**
   * Returns a new random id.
   * @return A UUID, or a time-and-random id where `randomUUID` is missing.
   */
  next(): string {
    // `randomUUID` exists only in secure contexts (HTTPS or localhost); the
    // fallback is unique enough for client-side document ids.
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
      return crypto.randomUUID();
    }
    return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  },
};
