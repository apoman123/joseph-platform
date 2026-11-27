/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Left vertical drawing toolbar built from the tool registry.
 *
 * Every control is a labelled button; armed tools and toggles report their
 * state through `aria-pressed`. Pressing a group icon with several tools
 * (`aria-haspopup="menu"`, `aria-expanded`) opens a `role="menu"` of
 * `menuitemradio` entries; Escape or a press outside closes it. Tooltips
 * name a tool's hotkey when it has one.
 */

'use client';
import type {ToolGroup, ToolPlugin} from '@joseph/drawing-core';
import {
  Eye,
  EyeOff,
  Lock,
  LockOpen,
  Magnet,
  MousePointer2,
  LayoutGrid,
  Redo2,
  Undo2,
  Star,
} from 'lucide-react';
import {useEffect, useRef, useState, type ReactElement} from 'react';
import {Icon} from './icons';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {DRAWING_FAVORITES, favoriteDrawings} from './drawing-favorites';

/**
 * Subscribes the component to the tool registry.
 * @return Registered tools in registration order.
 */
function useTools(): ToolPlugin[] {
  const runtime = useRuntime();
  const [tools, setTools] = useState(() => runtime.tools.list());
  useEffect(() => {
    setTools(runtime.tools.list());
    return runtime.tools.subscribe(() => void setTools(runtime.tools.list()));
  }, [runtime]);
  return tools;
}

/** Group names used in flyout labels. */
const GROUP_NAMES: Readonly<Record<ToolGroup, string>> = {
  lines: 'Lines',
  shapes: 'Shapes',
  fibonacci: 'Fibonacci',
  gann: 'Gann',
  patterns: 'Patterns',
  trading: 'Positions',
  measure: 'Measure',
  studies: 'Engine studies',
  annotation: 'Annotations',
};

/** Storage key of the last tool picked per group (JSON object). */
const GROUP_STORAGE_KEY = 'joseph.drawbar.groups';

/**
 * Groups tools by their category, preserving first registration order.
 * @param tools Registered tools.
 * @return Groups with their tools.
 */
export function groupTools(
  tools: readonly ToolPlugin[]
): Array<{group: ToolGroup; tools: ToolPlugin[]}> {
  const groups: Array<{group: ToolGroup; tools: ToolPlugin[]}> = [];
  for (const tool of tools) {
    const existing = groups.find(entry => entry.group === tool.manifest.group);
    if (existing) existing.tools.push(tool);
    else groups.push({group: tool.manifest.group, tools: [tool]});
  }
  return groups;
}

/**
 * Formats a hotkey hint for a tooltip, the way the top bar shows its
 * shortcuts: modifiers capitalized, the key in upper case, parts joined by
 * ` + `.
 * @param hotkey Combo such as `alt+t` or `alt+shift+r`.
 * @return Text such as `Alt + T` or `Alt + Shift + R`.
 */
export function hotkeyHint(hotkey: string): string {
  const parts = hotkey.split('+').map(part => part.trim().toLowerCase());
  const key = (parts.pop() ?? '').toUpperCase();
  const modifiers = parts.map(m => m.charAt(0).toUpperCase() + m.slice(1));
  return [...modifiers, key].join(' + ');
}

/** Flyout of one tool group. */
interface FlyoutProps {
  /** Group. */
  group: ToolGroup;
  /** Tools of the group. */
  tools: readonly ToolPlugin[];
  /** Armed tool id, or null for the pointer. */
  activeTool: string | null;
  /** Tool displayed on the group button. */
  selectedTool: ToolPlugin;
  /** User-selected stable favorite tool IDs. */
  favorites: readonly string[];
  /** Toggles a favorite without arming the tool or closing the menu. */
  onFavorite: (toolId: string) => void;
  /** Arms a tool. */
  onPick: (tool: ToolPlugin) => void;
}

/**
 * Renders the group icon that opens its tool list beside the toolbar.
 * @param props Props.
 * @return Flyout trigger and panel.
 */
function GroupFlyout({
  group,
  tools,
  activeTool,
  selectedTool,
  favorites,
  onFavorite,
  onPick,
}: FlyoutProps): ReactElement {
  const [position, setPosition] = useState<{left: number; top: number} | null>(
    null
  );
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!position) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return;
      // Presses on the panel or its trigger (which toggles) keep it open.
      if (
        event instanceof PointerEvent &&
        (panel.current?.contains(event.target as Node) ||
          trigger.current?.contains(event.target as Node))
      ) {
        return;
      }
      setPosition(null);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', close);
    };
  }, [position]);
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="drawbar__btn"
        aria-label={`${GROUP_NAMES[group]} drawing tools`}
        title={`Choose ${GROUP_NAMES[group].toLowerCase()} drawing tool`}
        aria-pressed={activeTool === selectedTool.manifest.id}
        aria-haspopup="menu"
        aria-expanded={position !== null}
        data-testid={`tool-group-${group}`}
        onClick={() => {
          // The flyout is fixed-positioned beside the trigger, so the
          // scrolling toolbar cannot clip it.
          const rect = trigger.current?.getBoundingClientRect();
          setPosition(
            position || !rect ? null : {left: rect.right + 6, top: rect.top - 4}
          );
        }}
      >
        <Icon name={selectedTool.manifest.icon} />
      </button>
      {position && (
        <div
          ref={panel}
          className="drawbar__flyout j-menu"
          role="menu"
          aria-label={`${GROUP_NAMES[group]} tools`}
          style={{left: position.left, top: position.top}}
        >
          {tools.map(tool => (
            <div
              key={tool.manifest.id}
              role="none"
              className="drawbar__favorite-row"
            >
              <button
                type="button"
                role="menuitemradio"
                aria-checked={activeTool === tool.manifest.id}
                className="j-menu__item drawbar__option"
                data-testid={`tool-option-${tool.manifest.id}`}
                onClick={() => {
                  onPick(tool);
                  setPosition(null);
                }}
              >
                <Icon name={tool.manifest.icon} size={16} />
                <span>{tool.manifest.displayName}</span>
                {tool.manifest.hotkey && (
                  <kbd>{hotkeyHint(tool.manifest.hotkey)}</kbd>
                )}
              </button>
              <button
                type="button"
                role="menuitemcheckbox"
                aria-label={`Favorite ${tool.manifest.displayName}`}
                aria-checked={favorites.includes(tool.manifest.id)}
                className="j-btn j-btn--ghost j-btn--icon"
                onClick={() => void onFavorite(tool.manifest.id)}
              >
                <Star
                  size={15}
                  aria-hidden="true"
                  fill={
                    favorites.includes(tool.manifest.id)
                      ? 'currentColor'
                      : 'none'
                  }
                />
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/**
 * Renders the drawing toolbar for the active chart.
 * @return Navigation element.
 */
export function DrawingToolbar(): ReactElement {
  const runtime = useRuntime();
  const registered = useTools();
  const tools = registered.some(tool =>
    tool.manifest.id.startsWith('reference.')
  )
    ? registered.filter(
        tool =>
          tool.manifest.id.startsWith('reference.') ||
          tool.manifest.id === 'zone-box' ||
          tool.seriesRequest !== undefined
      )
    : registered;
  const activeTool = useStore(runtime.drawing.activeTool);
  const magnet = useStore(runtime.drawing.magnet);
  useStore(runtime.drawing.revision);
  const selection = useStore(runtime.selection);
  const workspace = useStore(runtime.workspace);
  const favorites = favoriteDrawings(
    (workspace.panels as Record<string, unknown> | undefined)?.[
      DRAWING_FAVORITES
    ]
  );
  const toggleFavorite = (id: string) =>
    void runtime.dispatch({
      type: 'setPanel',
      featureId: DRAWING_FAVORITES,
      state: {
        ...favorites,
        tools: favorites.tools.includes(id)
          ? favorites.tools.filter(tool => tool !== id)
          : [...favorites.tools, id],
      },
    });
  const store = runtime.drawing.stores.get(selection.chartId);
  const drawings = store?.get().drawings ?? [];
  const allLocked = drawings.length > 0 && drawings.every(d => d.locked);
  const allHidden = drawings.length > 0 && drawings.every(d => !d.visible);
  const run = (id: string) =>
    void runtime.commands.execute(id).catch(() => undefined);
  const setAll = (patch: {locked?: boolean; visible?: boolean}) => {
    if (!store) return;
    for (const doc of store.get().drawings) {
      store.update(
        doc.drawing_id,
        d => ({...d, ...patch, updated_at_ms: Date.now()}),
        patch.locked !== undefined ? 'Lock drawings' : 'Show drawings'
      );
    }
    runtime.drawing.revision.update(n => n + 1);
  };
  const [chosen, setChosen] = useState<Record<string, string>>(() => {
    try {
      const raw = runtime.storage.getItem(GROUP_STORAGE_KEY);
      const parsed: unknown = raw ? JSON.parse(raw) : {};
      // Values are only compared with tool ids, so a wrong saved value
      // matches nothing.
      return parsed && typeof parsed === 'object'
        ? (parsed as Record<string, string>)
        : {};
    } catch {
      // Unreadable saved state: start with each group's first tool.
      return {};
    }
  });
  const pick = (tool: ToolPlugin) => {
    const next = {...chosen, [tool.manifest.group]: tool.manifest.id};
    setChosen(next);
    runtime.storage.setItem(GROUP_STORAGE_KEY, JSON.stringify(next));
    run(`drawing.tool.${tool.manifest.id}`);
  };
  if (
    workspace.charts.find(chart => chart.chart_id === selection.chartId)
      ?.view_type === 'stock_heatmap'
  ) {
    return (
      <nav className="drawbar" aria-label="Heatmap controls">
        <span title="Select a stock tile to open its candle chart">
          <LayoutGrid size={18} aria-hidden="true" />
        </span>
      </nav>
    );
  }
  return (
    <nav className="drawbar" aria-label="Drawing tools">
      <button
        type="button"
        className="drawbar__btn"
        aria-label="Pointer"
        title="Pointer (Esc)"
        aria-pressed={activeTool === null}
        onClick={() => void run('drawing.pointer')}
      >
        <MousePointer2 size={18} aria-hidden="true" />
      </button>
      {groupTools(tools)
        .sort(
          (a, b) =>
            [
              'lines',
              'fibonacci',
              'gann',
              'patterns',
              'shapes',
              'annotation',
              'trading',
              'measure',
              'studies',
            ].indexOf(a.group) -
            [
              'lines',
              'fibonacci',
              'gann',
              'patterns',
              'shapes',
              'annotation',
              'trading',
              'measure',
              'studies',
            ].indexOf(b.group)
        )
        .map(({group, tools: members}) => {
          const armed = members.find(t => t.manifest.id === activeTool);
          const tool =
            armed ??
            members.find(t => t.manifest.id === chosen[group]) ??
            members[0];
          if (!tool) return null;
          return (
            <div key={group} className="drawbar__group">
              <span className="drawbar__sep" aria-hidden="true" />
              <div className="drawbar__slot">
                {members.length === 1 ? (
                  <button
                    type="button"
                    className="drawbar__btn"
                    aria-label={tool.manifest.displayName}
                    title={
                      tool.manifest.hotkey
                        ? `${tool.manifest.displayName} (${hotkeyHint(tool.manifest.hotkey)})`
                        : tool.manifest.displayName
                    }
                    aria-pressed={activeTool === tool.manifest.id}
                    data-testid={`tool-${tool.manifest.id}`}
                    onClick={() => void pick(tool)}
                  >
                    <Icon name={tool.manifest.icon} />
                  </button>
                ) : (
                  <GroupFlyout
                    group={group}
                    tools={members}
                    activeTool={activeTool}
                    selectedTool={tool}
                    favorites={favorites.tools}
                    onFavorite={toggleFavorite}
                    onPick={pick}
                  />
                )}
              </div>
            </div>
          );
        })}
      <span className="drawbar__sep" aria-hidden="true" />
      <button
        type="button"
        className="drawbar__btn"
        aria-label="Undo"
        title="Undo (Ctrl + Z)"
        disabled={!store?.canUndo()}
        data-testid="undo"
        onClick={() => void run('drawing.undo')}
      >
        <Undo2 size={18} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="drawbar__btn"
        aria-label="Redo"
        title="Redo (Ctrl + Shift + Z)"
        disabled={!store?.canRedo()}
        data-testid="redo"
        onClick={() => void run('drawing.redo')}
      >
        <Redo2 size={18} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="drawbar__btn"
        aria-label="Magnet snapping to candles"
        title="Magnet: snap to open, high, low or close (hold Ctrl to invert)"
        aria-pressed={magnet}
        onClick={() => void run('drawing.magnet')}
      >
        <Magnet size={18} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="drawbar__btn"
        aria-label={allLocked ? 'Unlock all drawings' : 'Lock all drawings'}
        aria-pressed={allLocked}
        disabled={drawings.length === 0}
        onClick={() => void setAll({locked: !allLocked})}
      >
        {allLocked ? (
          <Lock size={18} aria-hidden="true" />
        ) : (
          <LockOpen size={18} aria-hidden="true" />
        )}
      </button>
      <button
        type="button"
        className="drawbar__btn"
        aria-label={allHidden ? 'Show all drawings' : 'Hide all drawings'}
        aria-pressed={allHidden}
        disabled={drawings.length === 0}
        onClick={() => void setAll({visible: allHidden})}
      >
        {allHidden ? (
          <EyeOff size={18} aria-hidden="true" />
        ) : (
          <Eye size={18} aria-hidden="true" />
        )}
      </button>
    </nav>
  );
}
