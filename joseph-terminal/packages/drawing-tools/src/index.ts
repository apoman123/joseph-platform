/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview First-party drawing tools. Each tool lives in its own
 * directory and is also available as a subpath export
 * (`@joseph/drawing-tools/trendline`). Removing a tool from
 * `FIRST_PARTY_TOOLS` hides it; saved drawings stay readable as
 * unsupported placeholders.
 */

import type {ToolPlugin} from '@joseph/drawing-core';
import {abcdPatternTool} from './abcd-pattern';
import {anchoredVwapTool} from './anchored-vwap';
import {arcTool} from './arc';
import {arrowTool} from './arrow';
import {brushTool} from './brush';
import {circleTool} from './circle';
import {curveTool} from './curve';
import {elliottCorrectionTool} from './elliott-correction';
import {elliottImpulseTool} from './elliott-impulse';
import {ellipseTool} from './ellipse';
import {extendedLineTool} from './extended-line';
import {fibCirclesTool} from './fib-circles';
import {fibExtensionTool} from './fib-extension';
import {fibRetracementTool} from './fib-retracement';
import {fibTimeZonesTool} from './fib-time-zones';
import {gannBoxTool} from './gann-box';
import {gannFanTool} from './gann-fan';
import {headAndShouldersTool} from './head-and-shoulders';
import {horizontalLineTool} from './horizontal-line';
import {horizontalRayTool} from './horizontal-ray';
import {longPositionTool} from './long-position';
import {parallelChannelTool} from './parallel-channel';
import {pitchforkTool} from './pitchfork';
import {polylineTool} from './polyline';
import {priceRangeTool} from './price-range';
import {rayTool} from './ray';
import {rectangleTool} from './rectangle';
import {regressionChannelTool} from './regression-channel';
import {rotatedRectangleTool} from './rotated-rectangle';
import {shortPositionTool} from './short-position';
import {textTool} from './text';
import {trendlineTool} from './trendline';
import {triangleTool} from './triangle';
import {trianglePatternTool} from './triangle-pattern';
import {verticalLineTool} from './vertical-line';
import {xabcdPatternTool} from './xabcd-pattern';
import {zoneBoxTool} from './zone-box';

export {
  abcdPatternTool,
  anchoredVwapTool,
  arcTool,
  arrowTool,
  brushTool,
  circleTool,
  curveTool,
  elliottCorrectionTool,
  elliottImpulseTool,
  ellipseTool,
  extendedLineTool,
  fibCirclesTool,
  fibExtensionTool,
  fibRetracementTool,
  fibTimeZonesTool,
  gannBoxTool,
  gannFanTool,
  headAndShouldersTool,
  horizontalLineTool,
  horizontalRayTool,
  longPositionTool,
  parallelChannelTool,
  pitchforkTool,
  polylineTool,
  priceRangeTool,
  rayTool,
  rectangleTool,
  regressionChannelTool,
  rotatedRectangleTool,
  shortPositionTool,
  textTool,
  trendlineTool,
  triangleTool,
  trianglePatternTool,
  verticalLineTool,
  xabcdPatternTool,
  zoneBoxTool,
};

/**
 * Tools shipped with the terminal, in toolbar order (grouped). Anchored
 * VWAP and the regression channel are engine-backed: they request their
 * values from the market engine (`/v1/indicators/series`) and only draw
 * them; nothing is computed in the browser.
 */
export const FIRST_PARTY_TOOLS: readonly ToolPlugin[] = [
  trendlineTool,
  rayTool,
  extendedLineTool,
  horizontalLineTool,
  horizontalRayTool,
  verticalLineTool,
  parallelChannelTool,
  pitchforkTool,
  rectangleTool,
  zoneBoxTool,
  polylineTool,
  ellipseTool,
  circleTool,
  triangleTool,
  rotatedRectangleTool,
  arcTool,
  curveTool,
  fibRetracementTool,
  fibExtensionTool,
  fibTimeZonesTool,
  fibCirclesTool,
  gannFanTool,
  gannBoxTool,
  xabcdPatternTool,
  abcdPatternTool,
  headAndShouldersTool,
  elliottImpulseTool,
  elliottCorrectionTool,
  trianglePatternTool,
  longPositionTool,
  shortPositionTool,
  priceRangeTool,
  anchoredVwapTool,
  regressionChannelTool,
  textTool,
  arrowTool,
  brushTool,
];
