/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Workspace-persisted favorite drawings with a movable toolbar. */

'use client';

import {GripVertical, RotateCw} from 'lucide-react';
import {useRef, useState, type ReactElement} from 'react';
import {Icon} from './icons';
import {useRuntime} from './runtime-context';
import {useStore} from './react-bindings';
import styles from './drawing-favorites.module.css';

/** Workspace panel key for tools, orientation and last committed position. */
export const DRAWING_FAVORITES = 'shell.drawing-favorites';

/** Validated favorite toolbar state stored in the workspace document. */
export interface FavoriteDrawings {
  /** Stable registered tool IDs, preserving the user's chosen order. */
  tools: string[];
  /** Horizontal viewport offset in CSS pixels. */
  left: number;
  /** Vertical viewport offset in CSS pixels. */
  top: number;
  /** Whether the buttons form a vertical rather than horizontal strip. */
  vertical: boolean;
}

/**
 * Reads a bounded favorite-tool configuration from an imported workspace.
 * @param value Untrusted workspace panel value, or undefined on first use.
 * @return Safe state; unknown tool IDs remain stored for removable features.
 */
export function favoriteDrawings(value: unknown): FavoriteDrawings {
  const saved =
    value && typeof value === 'object'
      ? (value as Record<string, unknown>)
      : {};
  return {
    tools: Array.isArray(saved.tools)
      ? [
          ...new Set(
            saved.tools.filter(
              (id): id is string => typeof id === 'string' && id.length <= 128
            )
          ),
        ].slice(0, 128)
      : [],
    left:
      typeof saved.left === 'number' && Number.isFinite(saved.left)
        ? Math.max(48, saved.left)
        : 76,
    top:
      typeof saved.top === 'number' && Number.isFinite(saved.top)
        ? Math.max(44, saved.top)
        : 116,
    vertical: saved.vertical === true,
  };
}

/**
 * Renders favorite drawing commands without owning chart or drawing state.
 * @return Movable toolbar, or null when no installed favorite is selected.
 */
export function DrawingFavorites(): ReactElement | null {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const active = useStore(runtime.drawing.activeTool);
  const saved = favoriteDrawings(
    (workspace.panels as Record<string, unknown> | undefined)?.[
      DRAWING_FAVORITES
    ]
  );
  const [moving, setMoving] = useState<{left: number; top: number} | null>(
    null
  );
  const drag = useRef<{x: number; y: number; left: number; top: number} | null>(
    null
  );
  const tools = saved.tools.flatMap(id => {
    const tool = runtime.tools.get(id);
    return tool ? [tool] : [];
  });
  const commit = (patch: Partial<FavoriteDrawings>) =>
    void runtime.dispatch({
      type: 'setPanel',
      featureId: DRAWING_FAVORITES,
      state: {...saved, ...patch},
    });
  const position = moving ?? saved;
  const viewportWidth =
    typeof window === 'undefined' ? 1024 : window.innerWidth;
  const viewportHeight =
    typeof window === 'undefined' ? 768 : window.innerHeight;
  const clamp = (left: number, top: number) => ({
    left: Math.max(48, Math.min(left, viewportWidth - 100)),
    top: Math.max(44, Math.min(top, viewportHeight - 60)),
  });
  if (
    !tools.length ||
    workspace.charts.find(chart => chart.chart_id === workspace.active_chart_id)
      ?.view_type === 'stock_heatmap'
  ) {
    return null;
  }
  return (
    <div
      className={styles.bar}
      role="toolbar"
      aria-label="Favorite drawing tools"
      aria-orientation={saved.vertical ? 'vertical' : 'horizontal'}
      style={{
        left: Math.min(position.left, Math.max(48, viewportWidth - 100)),
        top: Math.min(position.top, Math.max(44, viewportHeight - 60)),
        flexDirection: saved.vertical ? 'column' : 'row',
      }}
    >
      <button
        className="j-btn j-btn--ghost j-btn--icon"
        aria-label="Move favorite drawing tools"
        title="Drag to move; arrow keys move 8 pixels"
        onPointerDown={event => {
          event.preventDefault();
          event.stopPropagation();
          drag.current = {
            x: event.clientX,
            y: event.clientY,
            left: saved.left,
            top: saved.top,
          };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={event => {
          const origin = drag.current;
          if (origin) {
            setMoving(
              clamp(
                origin.left + event.clientX - origin.x,
                origin.top + event.clientY - origin.y
              )
            );
          }
        }}
        onPointerUp={event => {
          const origin = drag.current;
          if (!origin) return;
          commit(
            clamp(
              origin.left + event.clientX - origin.x,
              origin.top + event.clientY - origin.y
            )
          );
          drag.current = null;
          setMoving(null);
        }}
        onPointerCancel={() => {
          drag.current = null;
          setMoving(null);
        }}
        onKeyDown={event => {
          const offset = {
            ArrowLeft: [-8, 0],
            ArrowRight: [8, 0],
            ArrowUp: [0, -8],
            ArrowDown: [0, 8],
          }[event.key];
          if (!offset) return;
          event.preventDefault();
          event.stopPropagation();
          commit(
            clamp(saved.left + (offset[0] ?? 0), saved.top + (offset[1] ?? 0))
          );
        }}
      >
        <GripVertical size={16} aria-hidden="true" />
      </button>
      {tools.map(tool => (
        <button
          key={tool.manifest.id}
          className="j-btn j-btn--ghost j-btn--icon"
          aria-label={`Draw ${tool.manifest.displayName}`}
          title={tool.manifest.displayName}
          aria-pressed={active === tool.manifest.id}
          onClick={() =>
            void runtime.commands
              .execute(`drawing.tool.${tool.manifest.id}`)
              .catch(
                error => void runtime.notifier.notify(String(error), 'error')
              )
          }
        >
          <Icon name={tool.manifest.icon} size={17} />
        </button>
      ))}
      <button
        className="j-btn j-btn--ghost j-btn--icon"
        aria-label="Vertical favorite drawing tools"
        aria-pressed={saved.vertical}
        onClick={() => void commit({vertical: !saved.vertical})}
      >
        <RotateCw size={16} aria-hidden="true" />
      </button>
    </div>
  );
}
