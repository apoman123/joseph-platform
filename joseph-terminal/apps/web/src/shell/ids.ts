/**
 * @fileoverview Client-side identifiers for UI documents (drawings).
 */

/** Id generator namespace. */
export const ids = {
  /**
   * Returns a new random id.
   * @return Id string.
   */
  next(): string {
    if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
      return crypto.randomUUID();
    }
    return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  },
};
