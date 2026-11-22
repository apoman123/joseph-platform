/**
 * @fileoverview First-party drawing tools. Each tool lives in its own
 * directory and is also available as a subpath export
 * (`@joseph/drawing-tools/trendline`). Removing a tool from
 * `FIRST_PARTY_TOOLS` hides it; saved drawings stay readable as
 * unsupported placeholders.
 */
import type {ToolPlugin} from '@joseph/drawing-core';
import {arrowTool} from './arrow';
import {brushTool} from './brush';
import {extendedLineTool} from './extended-line';
import {fibExtensionTool} from './fib-extension';
import {fibRetracementTool} from './fib-retracement';
import {horizontalLineTool} from './horizontal-line';
import {horizontalRayTool} from './horizontal-ray';
import {longPositionTool} from './long-position';
import {parallelChannelTool} from './parallel-channel';
import {polylineTool} from './polyline';
import {priceRangeTool} from './price-range';
import {rayTool} from './ray';
import {rectangleTool} from './rectangle';
import {shortPositionTool} from './short-position';
import {textTool} from './text';
import {trendlineTool} from './trendline';
import {verticalLineTool} from './vertical-line';
import {zoneBoxTool} from './zone-box';

export {
  arrowTool,
  brushTool,
  extendedLineTool,
  fibExtensionTool,
  fibRetracementTool,
  horizontalLineTool,
  horizontalRayTool,
  longPositionTool,
  parallelChannelTool,
  polylineTool,
  priceRangeTool,
  rayTool,
  rectangleTool,
  shortPositionTool,
  textTool,
  trendlineTool,
  verticalLineTool,
  zoneBoxTool,
};

/**
 * Tools shipped with the terminal, in toolbar order (grouped). Anchored
 * VWAP is intentionally absent: it would compute a volume-weighted price in
 * the browser, and indicator values come from the engine only.
 */
export const FIRST_PARTY_TOOLS: readonly ToolPlugin[] = [
  trendlineTool,
  rayTool,
  extendedLineTool,
  horizontalLineTool,
  horizontalRayTool,
  verticalLineTool,
  parallelChannelTool,
  rectangleTool,
  zoneBoxTool,
  polylineTool,
  fibRetracementTool,
  fibExtensionTool,
  longPositionTool,
  shortPositionTool,
  priceRangeTool,
  textTool,
  arrowTool,
  brushTool,
];
