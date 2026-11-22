/**
 * @fileoverview XABCD harmonic pattern (Gartley, bat, butterfly, crab...).
 *
 * - Anchors: five, placed in order X, A, B, C, D (time/price).
 * - Geometry: solid legs X-A-B-C-D; dashed connectors X-B, A-C, B-D and
 *   X-D carry the harmonic ratios, computed from the anchor prices with
 *   exact decimals (pure geometry, never market data):
 *   AB/XA = |B-A| / |A-X| (on X-B), BC/AB = |C-B| / |B-A| (on A-C),
 *   CD/BC = |D-C| / |C-B| (on B-D) and AD/XA = |D-A| / |A-X|, the
 *   retracement of XA at D (on X-D). Triangles XAB and BCD are filled.
 * - Hit-testing: handles first, then any leg or connector within the
 *   tolerance, then the inside of a filled triangle.
 * - Handles: the five anchors (handle index = anchor index).
 * - Style options: color, width, dash, fill on/off (default on) and labels
 *   (point letters and ratios).
 */
import {createPatternTool, type PatternSpec} from '@joseph/drawing-core';

/** Pattern geometry: labels, ratio connectors and filled triangles. */
export const XABCD_SPEC: PatternSpec = {
  labels: ['X', 'A', 'B', 'C', 'D'],
  ratios: [
    {leg: [1, 2], of: [0, 1], along: [0, 2]},
    {leg: [2, 3], of: [1, 2], along: [1, 3]},
    {leg: [3, 4], of: [2, 3], along: [2, 4]},
    {leg: [1, 4], of: [0, 1], along: [0, 4]},
  ],
  fills: [
    [0, 1, 2],
    [2, 3, 4],
  ],
};

/** XABCD harmonic pattern tool. */
export const xabcdPatternTool = createPatternTool(
  {
    id: 'xabcd-pattern',
    version: 1,
    displayName: 'XABCD pattern',
    group: 'patterns',
    icon: 'chart-network',
    anchorCount: 5,
  },
  XABCD_SPEC,
  'on'
);
