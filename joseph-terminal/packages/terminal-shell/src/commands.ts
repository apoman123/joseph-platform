/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Default hotkeys and the command-palette model.
 */

/** Default key bindings (normalized combos to command ids). */
export const DEFAULT_HOTKEYS: Readonly<Record<string, string>> = {
  'mod+k': 'palette.open',
  'mod+z': 'drawing.undo',
  'shift+mod+z': 'drawing.redo',
  'mod+y': 'drawing.redo',
  'escape': 'drawing.cancel',
  'delete': 'drawing.delete',
  'backspace': 'drawing.delete',
  'alt+t': 'drawing.tool.trendline',
  'alt+h': 'drawing.tool.horizontal-line',
  'alt+r': 'drawing.tool.rectangle',
  'alt+f': 'drawing.tool.fib-retracement',
  'alt+m': 'drawing.tool.price-range',
  'alt+1': 'layout.1',
  'alt+2': 'layout.2h',
  'alt+4': 'layout.4',
  'alt+8': 'layout.8',
};

/** Palette entry. */
export interface PaletteEntry {
  /** Command id. */
  id: string;
  /** Title. */
  title: string;
  /** Shortcut hint. */
  shortcut?: string;
}

/**
 * Scores how well a title matches a query (subsequence with bonuses).
 * @param title Title.
 * @param query Query.
 * @return Score; -1 when it does not match.
 */
export function matchScore(title: string, query: string): number {
  const t = title.toLowerCase();
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  if (t.startsWith(q)) return 1000 - t.length;
  const word = t.split(/\s+/).some(part => part.startsWith(q));
  if (word) return 800 - t.length;
  let score = 0;
  let index = 0;
  let previous = -2;
  for (const char of q) {
    const found = t.indexOf(char, index);
    if (found < 0) return -1;
    score += found === previous + 1 ? 10 : 1;
    previous = found;
    index = found + 1;
  }
  return score;
}

/**
 * Filters and ranks palette entries.
 * @param entries Entries.
 * @param query Query.
 * @param limit Maximum results.
 * @return Ranked entries.
 */
export function filterPalette(
  entries: readonly PaletteEntry[],
  query: string,
  limit = 12
): PaletteEntry[] {
  return entries
    .map(entry => ({entry, score: matchScore(entry.title, query)}))
    .filter(item => item.score >= 0)
    .sort(
      (a, b) => b.score - a.score || a.entry.title.localeCompare(b.entry.title)
    )
    .slice(0, limit)
    .map(item => item.entry);
}

/** Palette UI state. */
export interface PaletteState {
  /** Whether the palette is open. */
  open: boolean;
  /** Query text. */
  query: string;
  /** Highlighted index. */
  index: number;
}

/** Palette actions. */
export type PaletteAction =
  | {type: 'open'}
  | {type: 'close'}
  | {type: 'query'; query: string}
  | {type: 'move'; delta: number; count: number};

/**
 * Pure palette reducer.
 * @param state State.
 * @param action Action.
 * @return Next state.
 */
export function paletteReducer(
  state: PaletteState,
  action: PaletteAction
): PaletteState {
  switch (action.type) {
    case 'open':
      return {open: true, query: '', index: 0};
    case 'close':
      return {...state, open: false};
    case 'query':
      return {...state, query: action.query, index: 0};
    case 'move': {
      if (action.count <= 0) return {...state, index: 0};
      const index = (state.index + action.delta + action.count) % action.count;
      return {...state, index};
    }
    default:
      return state;
  }
}
