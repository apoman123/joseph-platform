/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Runtime contract validation for untrusted automation documents. */

import {workspaceValid, drawingValid} from './generated/validators.cjs';
import type {WorkspaceDocument, DrawingDocument} from './generated/contracts';

/**
 * Validates an entire workspace before automated replacement.
 * @param value Untrusted JSON document, including enum and range values.
 * @return True only for a complete canonical workspace document.
 */
export function isWorkspaceContract(
  value: unknown
): value is WorkspaceDocument {
  return workspaceValid(value);
}

/**
 * Validates drawing structure, anchors, styling and lifecycle fields.
 * @param value Untrusted JSON drawing document.
 * @return True only for a canonical drawing document.
 */
export function isDrawingContract(value: unknown): value is DrawingDocument {
  return drawingValid(value);
}
