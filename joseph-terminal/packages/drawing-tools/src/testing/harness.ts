/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Shared test harness for drawing tools (test-only module).
 */

import type {DrawingAnchor, DrawingDocument} from '@joseph/contracts';
import {
  createDrawing,
  type PixelTransform,
  type RenderState,
  type ToolPlugin,
} from '@joseph/drawing-core';

/** Linear transform: 10px per minute; price 200 at y=0 and 100 at y=500. */
export const TRANSFORM: PixelTransform = {
  width: 1000,
  height: 500,
  timeToX: t => (t / 60_000) * 10,
  xToTime: x => (x / 10) * 60_000,
  priceToY: p => ((200 - p) / 100) * 500,
  yToPrice: y => 200 - (y / 500) * 100,
};

/** Render state used by tests. */
export const STATE: RenderState = {
  selected: true,
  preview: false,
  tickSize: '0.1',
  durationMs: 60_000,
  palette: {
    user: '#e66fd2',
    text: '#8fa5b3',
    bid: '#3cc79a',
    ask: '#f0645a',
    background: '#0e1c25',
    fontFamily: 'Archivo',
  },
};

/**
 * Builds a document for a tool, expanding anchors like the controller.
 * @param tool Tool.
 * @param anchors User anchors.
 * @return Document.
 */
export function makeDoc(
  tool: ToolPlugin,
  anchors: DrawingAnchor[]
): DrawingDocument {
  const complete =
    tool.expandAnchors?.(anchors, {durationMs: 60_000, tickSize: '0.1'}) ??
    anchors;
  return createDrawing({
    toolId: tool.manifest.id,
    toolVersion: tool.manifest.version,
    instrumentId: 'mock:perp:BTCUSD',
    timeframe: null,
    anchors: complete,
    style: tool.defaultStyle(STATE.palette),
    props: tool.defaultProps?.(),
    text: tool.manifest.id === 'text' ? 'Liquidity above' : null,
    zIndex: 1,
    nowMs: 0,
    newId: () => 'd1',
  });
}

/** Recorded canvas operation. */
export interface Op {
  /** Method or `set:prop`. */
  op: string;
  /** Arguments. */
  args: unknown[];
}

/**
 * Renders a document into a recording context.
 * @param tool Tool.
 * @param doc Document.
 * @return Recorded operations.
 */
export function renderOps(tool: ToolPlugin, doc: DrawingDocument): Op[] {
  const ops: Op[] = [];
  const ctx = new Proxy<Record<string, unknown>>(
    {},
    {
      get(target, prop: string) {
        if (prop === 'measureText') {
          return (text: string) => ({width: text.length * 6});
        }
        if (prop in target) return target[prop];
        return (...args: unknown[]) => {
          ops.push({op: prop, args});
        };
      },
      set(target, prop: string, value: unknown) {
        ops.push({op: `set:${prop}`, args: [value]});
        return true;
      },
    }
  ) as unknown as CanvasRenderingContext2D;
  tool.render(ctx, doc, TRANSFORM, STATE);
  return ops;
}

/**
 * Collects text drawn by a render.
 * @param ops Recorded operations.
 * @return Texts.
 */
export function texts(ops: readonly Op[]): string[] {
  return ops.filter(o => o.op === 'fillText').map(o => String(o.args[0]));
}

/**
 * Asserts the manifest basics shared by every tool.
 * @param tool Tool.
 * @return Problems found.
 */
export function manifestProblems(tool: ToolPlugin): string[] {
  const m = tool.manifest;
  const problems: string[] = [];
  if (!/^[a-z][a-z0-9-]*$/.test(m.id)) problems.push('id must be kebab-case');
  if (m.version < 1) problems.push('version must be >= 1');
  if (m.anchorCount < 1) problems.push('anchorCount must be >= 1');
  if (
    m.displayName !==
      m.displayName.charAt(0).toUpperCase() +
        m.displayName.slice(1).toLowerCase() &&
    !/[A-Z]{2}/.test(m.displayName)
  ) {
    problems.push('displayName must be sentence case');
  }
  if (tool.defaultStyle(STATE.palette).color !== STATE.palette.user) {
    problems.push('default color must be the user (course) color');
  }
  return problems;
}
