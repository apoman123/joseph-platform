/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Build-time feature composition.
 *
 * Removing a feature is a one-line change here (or setting its manifest
 * lifecycle to REMOVED); saved data stays readable.
 */

import {ALERTS_FEATURE} from '../features/alerts';
import {dataHealthFeature} from '../features/data-health/data-health-feature';
import {
  drawingFeature,
  toolFeatures,
} from '../features/drawing/drawing-feature';
import {marketInfoFeature} from '../features/market-info/market-info-feature';
import {PORTFOLIO_FEATURE} from '../features/portfolio';
import {SCREENER_FEATURE} from '../features/screener';
import {
  paletteFeature,
  themeFeature,
  workspaceFeature,
} from '../features/shell/shell-features';
import {chartToolsFeature} from '../features/shell/chart-tools-feature';
import {STRATEGY_FEATURE} from '../features/strategy';
import {TRADING_FEATURE} from '../features/trading';
import {WATCHLIST_FEATURE} from '../features/watchlist';
import type {WebFeature} from '../shell/react-bindings';
import type {TerminalRuntime} from './runtime';
import {pythonFeature} from '../shell/python-editor';
import {mcpFeature} from '../shell/mcp-control';

/**
 * Returns the shell-owned features: workspace, theme, palette, chart tools,
 * drawings, indicators, market info and data health. They receive the whole
 * runtime.
 * @param runtime Runtime the features are composed into.
 * @return Features in registration order.
 */
export function shellFeatures(runtime: TerminalRuntime): WebFeature[] {
  return [
    workspaceFeature(runtime),
    themeFeature(runtime),
    paletteFeature(runtime),
    chartToolsFeature(runtime),
    drawingFeature(runtime),
    ...toolFeatures(runtime),
    marketInfoFeature(),
    dataHealthFeature(),
    pythonFeature(runtime),
    mcpFeature(runtime),
  ];
}

/**
 * Returns the market, strategy and trading features. Each is removable by
 * deleting its line; its saved settings stay readable. They take no runtime:
 * they reach the terminal only through their scoped `FeatureContext`.
 * @return Features in registration order.
 */
export function extensionFeatures(): WebFeature[] {
  return [
    WATCHLIST_FEATURE,
    SCREENER_FEATURE,
    STRATEGY_FEATURE,
    TRADING_FEATURE,
    ALERTS_FEATURE,
    PORTFOLIO_FEATURE,
  ];
}

/**
 * Returns every feature in composition order: shell features first.
 * @param runtime Runtime the features are composed into.
 * @return Features.
 */
export function composeFeatures(runtime: TerminalRuntime): WebFeature[] {
  return [...shellFeatures(runtime), ...extensionFeatures()];
}
