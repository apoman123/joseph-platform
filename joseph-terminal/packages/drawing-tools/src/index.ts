/**
 * @fileoverview First-party drawing tools. Each tool lives in its own
 * directory and is also available as a subpath export
 * (`@joseph/drawing-tools/trendline`). Removing a tool from
 * `FIRST_PARTY_TOOLS` hides it; saved drawings stay readable as
 * unsupported placeholders.
 */
import type {ToolPlugin} from '@joseph/drawing-core';
import {fibExtensionTool} from './fib-extension';
import {fibRetracementTool} from './fib-retracement';
import {horizontalLineTool} from './horizontal-line';
import {horizontalRayTool} from './horizontal-ray';
import {longPositionTool} from './long-position';
import {parallelChannelTool} from './parallel-channel';
import {priceRangeTool} from './price-range';
import {rectangleTool} from './rectangle';
import {shortPositionTool} from './short-position';
import {textTool} from './text';
import {trendlineTool} from './trendline';
import {verticalLineTool} from './vertical-line';

export {
  fibExtensionTool,
  fibRetracementTool,
  horizontalLineTool,
  horizontalRayTool,
  longPositionTool,
  parallelChannelTool,
  priceRangeTool,
  rectangleTool,
  shortPositionTool,
  textTool,
  trendlineTool,
  verticalLineTool,
};

/** Tools shipped with the terminal, in toolbar order. */
export const FIRST_PARTY_TOOLS: readonly ToolPlugin[] = [
  trendlineTool,
  horizontalLineTool,
  horizontalRayTool,
  verticalLineTool,
  parallelChannelTool,
  rectangleTool,
  fibRetracementTool,
  fibExtensionTool,
  longPositionTool,
  shortPositionTool,
  priceRangeTool,
  textTool,
];
