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
import {indicatorsFeature} from '../features/indicators/indicators-feature';
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

/**
 * Shell-owned features (chart stack workstream).
 * @param runtime Runtime.
 * @return Features.
 */
export function shellFeatures(runtime: TerminalRuntime): WebFeature[] {
  return [
    workspaceFeature(runtime),
    themeFeature(runtime),
    paletteFeature(runtime),
    chartToolsFeature(runtime),
    drawingFeature(runtime),
    ...toolFeatures(runtime),
    indicatorsFeature(runtime),
    marketInfoFeature(runtime),
    dataHealthFeature(runtime),
  ];
}

/**
 * Market, strategy and trading features. Each is removable by deleting its
 * line; its saved settings stay readable.
 * @param _runtime Runtime (these features use only their FeatureContext).
 * @return Features.
 */
export function extensionFeatures(_runtime: TerminalRuntime): WebFeature[] {
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
 * All features in composition order.
 * @param runtime Runtime.
 * @return Features.
 */
export function composeFeatures(runtime: TerminalRuntime): WebFeature[] {
  return [...shellFeatures(runtime), ...extensionFeatures(runtime)];
}
