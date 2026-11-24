/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Text note: multi-line text whose top-left corner is
 * anchored to one time/price point.
 */

import {
  anchorPoints,
  drawHandles,
  hitHandles,
  pointInRect,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Characters per line before a note line is hard-wrapped. */
const MAX_LINE_CHARS = 48;

/**
 * Splits note text into display lines.
 * @param text Note text; `\n` starts a new line.
 * @return Lines, each at most `MAX_LINE_CHARS` characters (long lines are
 *     cut, not wrapped at word boundaries).
 */
export function noteLines(text: string): string[] {
  const lines: string[] = [];
  for (const paragraph of text.split('\n')) {
    let rest = paragraph;
    while (rest.length > MAX_LINE_CHARS) {
      lines.push(rest.slice(0, MAX_LINE_CHARS));
      rest = rest.slice(MAX_LINE_CHARS);
    }
    lines.push(rest);
  }
  return lines;
}

/** Text tool. */
export const textTool: ToolPlugin = {
  manifest: {
    id: 'text',
    version: 1,
    displayName: 'Text note',
    group: 'annotation',
    icon: 'type',
    anchorCount: 1,
  },
  defaultStyle: palette => ({
    color: palette.user,
    line_width: 1,
    line_style: 'solid',
    font_size: 13,
  }),
  render(ctx, doc, transform, state) {
    const [p] = anchorPoints(transform, doc) ?? [];
    if (!p) return;
    const size = doc.style.font_size ?? 13;
    ctx.font = `${size}px ${state.palette.fontFamily}`;
    ctx.fillStyle = doc.style.color;
    ctx.textBaseline = 'top';
    // Line height is the font size plus 3 px of leading.
    noteLines(doc.text ?? '').forEach(
      (line, i) => void ctx.fillText(line, p.x, p.y + i * (size + 3))
    );
    drawHandles(ctx, [p], state);
  },
  hitTest(doc, transform, point, tolerance) {
    const [p] = anchorPoints(transform, doc) ?? [];
    if (!p) return null;
    const handle = hitHandles([p], point, tolerance);
    if (handle) return handle;
    const size = doc.style.font_size ?? 13;
    const lines = noteLines(doc.text ?? '');
    // Estimates the text width at 0.55 em per character (no context here).
    const width = Math.max(...lines.map(l => l.length)) * size * 0.55;
    const rect = {x: p.x, y: p.y, width, height: lines.length * (size + 3)};
    return pointInRect(point, rect, tolerance) ? {part: 'body'} : null;
  },
  describe: doc => `Note: ${doc.text ?? ''}`,
};
