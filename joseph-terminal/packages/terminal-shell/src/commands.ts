/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Default hotkeys and the command-palette model.
 */

/**
 * Default key bindings: normalized combos (modifiers in `shift`, `alt`,
 * `mod` order, where `mod` is Ctrl or Cmd, then the lowercase key) mapped to
 * command ids.
 */
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

/** One command listed in the command palette. */
export interface PaletteEntry {
  /** Command id run when the entry is chosen. */
  id: string;
  /** Text shown and matched against the query. */
  title: string;
  /** Key combination shown next to the title, if the command has one. */
  shortcut?: string;
}

/**
 * Scores how well a title matches a query, case-insensitively: a title
 * prefix scores highest, then a word prefix, then the query's characters as
 * an in-order subsequence (consecutive characters score more). Shorter
 * titles win ties within the prefix tiers.
 * @param title Entry title.
 * @param query Text typed by the user; surrounding spaces are ignored.
 * @return Score, higher is better; 0 for an empty query; -1 when the title
 *     does not contain the query as a subsequence.
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
 * Filters and ranks palette entries by `matchScore`, breaking ties by title.
 * @param entries Entries to search.
 * @param query Text typed by the user.
 * @param limit Maximum number of entries returned.
 * @return Matching entries, best first.
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

/** Command palette UI state. */
export interface PaletteState {
  /** Whether the palette is open. */
  open: boolean;
  /** Text typed by the user. */
  query: string;
  /** Index of the highlighted entry in the filtered list. */
  index: number;
}

/**
 * Command palette actions. `move` shifts the highlight by `delta` within
 * `count` filtered entries, wrapping at both ends.
 */
export type PaletteAction =
  | {type: 'open'}
  | {type: 'close'}
  | {type: 'query'; query: string}
  | {type: 'move'; delta: number; count: number};

/**
 * Applies a command palette action (pure).
 * @param state Current state.
 * @param action Action to apply.
 * @return Next state; opening clears the query and the highlight.
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
