/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Elliott impulse wave (1-2-3-4-5).
 *
 * - Anchors: six, placed in order: the origin (0) and the ends of waves
 *   (1) to (5) (time/price).
 * - Geometry: a solid zig-zag through the anchors with the wave labels
 *   (0) to (5), each placed above a local high or below a local low.
 *   No ratios: wave rules are left to the trader.
 * - Hit-testing: handles first, then any wave segment within the
 *   tolerance.
 * - Handles: the six anchors (handle index = anchor index).
 * - Style options: color, width, dash and labels. No fill.
 */

import {createPatternTool, type PatternSpec} from '@joseph/drawing-core';

/** Pattern geometry: wave labels. */
export const ELLIOTT_IMPULSE_SPEC: PatternSpec = {
  labels: ['(0)', '(1)', '(2)', '(3)', '(4)', '(5)'],
  describe: anchors =>
    `Waves (1) to (5), ${anchors[0]?.price ?? '?'} to ${anchors[5]?.price ?? '?'}`,
};

/** Elliott impulse wave tool. */
export const elliottImpulseTool = createPatternTool(
  {
    id: 'elliott-impulse',
    version: 1,
    displayName: 'Elliott impulse wave',
    group: 'patterns',
    icon: 'audio-waveform',
    anchorCount: 6,
  },
  ELLIOTT_IMPULSE_SPEC
);
