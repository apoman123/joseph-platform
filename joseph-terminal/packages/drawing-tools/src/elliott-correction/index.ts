/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Elliott correction wave (A-B-C).
 *
 * - Anchors: four, placed in order: the origin (0) and the ends of waves
 *   (A), (B) and (C) (time/price).
 * - Geometry: a solid zig-zag through the anchors with the labels (0),
 *   (A), (B), (C), each placed above a local high or below a local low.
 * - Hit-testing: handles first, then any wave segment within the
 *   tolerance.
 * - Handles: the four anchors (handle index = anchor index).
 * - Style options: color, width, dash and labels. No fill.
 */

import {createPatternTool, type PatternSpec} from '@joseph/drawing-core';

/** Pattern geometry: wave labels. */
export const ELLIOTT_CORRECTION_SPEC: PatternSpec = {
  labels: ['(0)', '(A)', '(B)', '(C)'],
  describe: anchors =>
    `Waves (A) to (C), ${anchors[0]?.price ?? '?'} to ${anchors[3]?.price ?? '?'}`,
};

/** Elliott correction wave tool. */
export const elliottCorrectionTool = createPatternTool(
  {
    id: 'elliott-correction',
    version: 1,
    displayName: 'Elliott correction wave',
    group: 'patterns',
    icon: 'waves',
    anchorCount: 4,
  },
  ELLIOTT_CORRECTION_SPEC
);
