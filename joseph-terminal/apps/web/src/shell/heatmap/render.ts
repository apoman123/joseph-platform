/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tile text fitting shared by the DOM map and the PNG export,
 * and the canvas renderer behind "Download image" and "Copy image".
 *
 * Text sizes derive from the tile's CSS-pixel size: the title is as large
 * as fits (up to 32 px), the value line about 60% of it, and both disappear
 * on tiles too small to read. Glyphs are never stretched.
 */

import type {Box} from './treemap';

/** Fitted text of one tile. */
export interface TileText {
  /** Title (ticker or description), or null when hidden. */
  title: string | null;
  /** Title font size in px. */
  titleSize: number;
  /** Value line, or null when hidden. */
  value: string | null;
  /** Value font size in px. */
  valueSize: number;
  /** Letter badge diameter in px, or 0 without a badge. */
  badge: number;
}

/** Average glyph width relative to the font size (condensed UI font). */
const GLYPH = 0.6;

/** Average bold glyph width relative to the font size (titles). */
const BOLD_GLYPH = 0.72;

/**
 * Fits a tile's title, value line and badge into its box.
 * @param box Tile box in CSS pixels.
 * @param title Title text, or null for none.
 * @param value Value text, or null for none.
 * @param logo Whether a letter badge is wanted.
 * @return Fitted text; null parts are hidden.
 */
export function fitTileText(
  box: Box,
  title: string | null,
  value: string | null,
  logo: boolean
): TileText {
  const none: TileText = {
    title: null,
    titleSize: 0,
    value: null,
    valueSize: 0,
    badge: 0,
  };
  const usableW = box.w - 8;
  if (usableW < 14 || box.h < 12) return none;
  let shown = title;
  if (shown !== null && shown.length * BOLD_GLYPH * 9 > usableW) {
    // A long description is shortened to what fits at the minimum size.
    const chars = Math.floor(usableW / (BOLD_GLYPH * 9));
    shown = chars >= 4 ? `${shown.slice(0, chars - 1)}…` : null;
  }
  const label = shown ?? value;
  if (label === null) return none;
  const size = Math.floor(
    Math.min(
      32,
      box.h * 0.4,
      usableW / Math.max(2.5, label.length * BOLD_GLYPH)
    )
  );
  if (size < 8) return none;
  const valueSize = Math.max(8, Math.round(size * 0.62));
  const valueFits =
    shown !== null &&
    value !== null &&
    value.length * GLYPH * valueSize <= usableW &&
    box.h >= size * 1.25 + valueSize * 1.35 + 4;
  const badgeSize = Math.round(
    Math.min(40, Math.max(size * 1.1, Math.min(box.w, box.h) * 0.16))
  );
  const badge =
    logo &&
    badgeSize >= 14 &&
    badgeSize <= box.w - 8 &&
    box.w >= 34 &&
    box.h >= badgeSize + 4 + size * 1.2 + (valueFits ? valueSize * 1.3 : 0) + 6
      ? badgeSize
      : 0;
  return {
    title: shown,
    titleSize: shown === null ? 0 : size,
    value: shown === null ? null : valueFits ? value : null,
    valueSize: shown === null ? 0 : valueSize,
    badge,
  };
}

/**
 * The letter of a tile's badge.
 * @param symbol Ticker.
 * @return First letter or digit, upper case.
 */
export function badgeLetter(symbol: string): string {
  return (/[A-Za-z0-9]/.exec(symbol)?.[0] ?? '?').toUpperCase();
}

/** A tile ready to draw. */
export interface DrawnTile {
  /** Box in CSS pixels. */
  box: Box;
  /** Fill colour. */
  fill: string;
  /** Text colour. */
  ink: string;
  /** Fitted text. */
  text: TileText;
  /** Badge letter. */
  letter: string;
  /** Whether the legend hides the tile. */
  hidden: boolean;
}

/** A group frame ready to draw. */
export interface DrawnGroup {
  /** Frame box. */
  box: Box;
  /** Header strip, or null. */
  header: Box | null;
  /** Group name. */
  name: string | null;
}

/** Everything the PNG export draws. */
export interface ExportScene {
  /** Map width in CSS pixels. */
  width: number;
  /** Map height in CSS pixels. */
  height: number;
  /** Title line, e.g. `S&P 500 · Market cap · Change 1D, %`. */
  title: string;
  /** Footer line, e.g. provenance and time. */
  footer: string;
  /** Group frames. */
  groups: readonly DrawnGroup[];
  /** Tiles. */
  tiles: readonly DrawnTile[];
  /** Font family. */
  font: string;
  /** Map background. */
  background: string;
  /** Text colour of the title, headers and footer. */
  chrome: string;
}

const BAND = 28;

/**
 * Draws a scene onto a canvas at the given pixel ratio.
 * @param canvas Target canvas (resized here).
 * @param scene Scene.
 * @param ratio Device pixel ratio for a sharp image.
 * @return The canvas, or null without a 2D context.
 */
export function drawScene(
  canvas: HTMLCanvasElement,
  scene: ExportScene,
  ratio = 2
): HTMLCanvasElement | null {
  canvas.width = Math.round(scene.width * ratio);
  canvas.height = Math.round((scene.height + BAND * 2) * ratio);
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.scale(ratio, ratio);
  ctx.fillStyle = scene.background;
  ctx.fillRect(0, 0, scene.width, scene.height + BAND * 2);
  ctx.textBaseline = 'middle';
  ctx.fillStyle = scene.chrome;
  ctx.font = `600 14px ${scene.font}`;
  ctx.textAlign = 'left';
  ctx.fillText(scene.title, 8, BAND / 2, scene.width - 16);
  ctx.save();
  ctx.translate(0, BAND);
  for (const group of scene.groups) {
    if (!group.header || group.name === null) continue;
    ctx.fillStyle = '#2b2f35';
    ctx.fillRect(
      group.header.x,
      group.header.y,
      group.header.w,
      group.header.h
    );
    ctx.fillStyle = '#e8ecee';
    ctx.font = `600 11px ${scene.font}`;
    ctx.textAlign = 'left';
    ctx.fillText(
      group.name.toUpperCase(),
      group.header.x + 5,
      group.header.y + group.header.h / 2,
      group.header.w - 10
    );
  }
  for (const tile of scene.tiles) {
    const {box, text} = tile;
    ctx.fillStyle = tile.hidden ? scene.background : tile.fill;
    ctx.fillRect(box.x, box.y, box.w, box.h);
    if (tile.hidden || text.title === null) continue;
    const cx = box.x + box.w / 2;
    const block =
      text.badge +
      (text.badge ? 4 : 0) +
      text.titleSize +
      (text.value ? text.valueSize * 1.2 : 0);
    let y = box.y + (box.h - block) / 2;
    if (text.badge) {
      ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
      ctx.beginPath();
      ctx.arc(cx, y + text.badge / 2, text.badge / 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#16181b';
      ctx.font = `700 ${Math.round(text.badge * 0.55)}px ${scene.font}`;
      ctx.textAlign = 'center';
      ctx.fillText(tile.letter, cx, y + text.badge / 2);
      y += text.badge + 4;
    }
    ctx.fillStyle = tile.ink;
    ctx.textAlign = 'center';
    ctx.font = `600 ${text.titleSize}px ${scene.font}`;
    ctx.fillText(text.title, cx, y + text.titleSize / 2, box.w - 4);
    y += text.titleSize;
    if (text.value) {
      ctx.font = `400 ${text.valueSize}px ${scene.font}`;
      ctx.fillText(text.value, cx, y + text.valueSize * 0.6, box.w - 4);
    }
  }
  ctx.restore();
  ctx.fillStyle = scene.chrome;
  ctx.font = `400 11px ${scene.font}`;
  ctx.textAlign = 'left';
  ctx.fillText(
    scene.footer,
    8,
    scene.height + BAND + BAND / 2,
    scene.width - 16
  );
  return canvas;
}

/**
 * Renders a scene to a PNG blob.
 * @param scene Scene.
 * @return PNG blob.
 * @throws Error when the browser cannot draw or encode the image.
 */
export async function scenePng(scene: ExportScene): Promise<Blob> {
  const canvas = drawScene(document.createElement('canvas'), scene);
  if (!canvas) throw new Error('This browser cannot draw the image');
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(blob => {
      if (blob) resolve(blob);
      else reject(new Error('This browser cannot encode a PNG'));
    }, 'image/png');
  });
}
