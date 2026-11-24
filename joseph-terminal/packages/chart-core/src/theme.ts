/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Theme resolution from CSS design tokens and tone colors.
 */

import type {ChartTheme} from './chart-port';
import type {OverlayTone} from './overlay-model';

/** Fallback dark theme matching `tokens.css`. */
export const DEFAULT_CHART_THEME: ChartTheme = {
  background: '#0e1c25',
  grid: '#172b38',
  text: '#8fa5b3',
  border: '#2a4558',
  bid: '#3cc79a',
  ask: '#f0645a',
  user: '#e66fd2',
  crosshair: '#5d7686',
  fontFamily: 'Archivo, system-ui, sans-serif',
  warning: '#f2b544',
  replay: '#9c8cff',
  muted: '#647b89',
};

/** Reads CSS custom properties (injectable for tests). */
export type CssVariableReader = (name: string) => string;

/**
 * Resolves the chart theme from CSS variables.
 * @param read Reads a custom property value (e.g. via getComputedStyle).
 * @return Theme with fallbacks for missing tokens.
 */
export function themeFromCss(read: CssVariableReader): ChartTheme {
  const pick = (name: string, fallback: string) => {
    const value = read(name).trim();
    return value || fallback;
  };
  return {
    background: pick('--chart-bg', DEFAULT_CHART_THEME.background),
    grid: pick('--chart-grid', DEFAULT_CHART_THEME.grid),
    text: pick('--chart-text', DEFAULT_CHART_THEME.text),
    border: pick('--fathom', DEFAULT_CHART_THEME.border),
    bid: pick('--bid', DEFAULT_CHART_THEME.bid),
    ask: pick('--ask', DEFAULT_CHART_THEME.ask),
    user: pick('--course', DEFAULT_CHART_THEME.user),
    crosshair: pick('--chart-crosshair', DEFAULT_CHART_THEME.crosshair),
    fontFamily: pick('--font-ui', DEFAULT_CHART_THEME.fontFamily),
    warning: pick('--sodium', DEFAULT_CHART_THEME.warning ?? '#f2b544'),
    replay: pick('--replay', DEFAULT_CHART_THEME.replay ?? '#9c8cff'),
    muted: pick('--haze-dim', DEFAULT_CHART_THEME.muted ?? '#647b89'),
  };
}

/**
 * Maps an overlay tone to a theme color.
 * @param theme Chart theme.
 * @param tone Overlay tone.
 * @return CSS color.
 */
export function toneColor(theme: ChartTheme, tone: OverlayTone): string {
  switch (tone) {
    case 'bid':
      return theme.bid;
    case 'ask':
    case 'danger':
      return theme.ask;
    case 'user':
      return theme.user;
    case 'warning':
      return theme.warning ?? theme.user;
    case 'muted':
      return theme.muted ?? theme.text;
    case 'neutral':
    default:
      return theme.text;
  }
}

/**
 * Adds alpha to a `#rrggbb` color.
 * @param color CSS color; only `#rrggbb` (any case) is converted.
 * @param alpha Opacity 0..1 (clamped).
 * @return `rgba(...)` string, or the input when not a 6-digit hex.
 */
export function withAlpha(color: string, alpha: number): string {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(
    color.trim()
  );
  if (!match) return color;
  const [r, g, b] = [match[1], match[2], match[3]].map(h =>
    parseInt(h ?? '0', 16)
  );
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha))})`;
}
