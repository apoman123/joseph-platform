/**
 * @fileoverview Left vertical drawing toolbar built from the tool registry.
 */
'use client';
import type {ToolGroup, ToolPlugin} from '@joseph/drawing-core';
import {
  ChevronRight,
  Eye,
  EyeOff,
  Lock,
  LockOpen,
  Magnet,
  MousePointer2,
  Redo2,
  Undo2,
} from 'lucide-react';
import {useEffect, useRef, useState, type ReactElement} from 'react';
import {Icon} from './icons';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';

/**
 * Subscribes to the tool registry.
 * @return Registered tools.
 */
function useTools(): ToolPlugin[] {
  const runtime = useRuntime();
  const [tools, setTools] = useState(() => runtime.tools.list());
  useEffect(() => {
    setTools(runtime.tools.list());
    return runtime.tools.subscribe(() => setTools(runtime.tools.list()));
  }, [runtime]);
  return tools;
}

/** Group names used in flyout labels. */
const GROUP_NAMES: Readonly<Record<ToolGroup, string>> = {
  lines: 'Lines',
  shapes: 'Shapes',
  fibonacci: 'Fibonacci',
  trading: 'Positions',
  measure: 'Measure',
  annotation: 'Annotations',
};

/** Storage key of the last tool used per group. */
const GROUP_STORAGE_KEY = 'joseph.drawbar.groups';

/**
 * Groups tools in toolbar order.
 * @param tools Registered tools.
 * @return Groups with their tools.
 */
export function groupTools(
  tools: readonly ToolPlugin[]
): {group: ToolGroup; tools: ToolPlugin[]}[] {
  const groups: {group: ToolGroup; tools: ToolPlugin[]}[] = [];
  for (const tool of tools) {
    const last = groups.at(-1);
    if (last && last.group === tool.manifest.group) last.tools.push(tool);
    else groups.push({group: tool.manifest.group, tools: [tool]});
  }
  return groups;
}

/**
 * Formats a hotkey hint for a tooltip.
 * @param hotkey Combo such as `alt+t`.
 * @return Text such as `ALT + T`.
 */
function hotkeyHint(hotkey: string): string {
  return hotkey.replace('alt', 'Alt').replace('+', ' + ').toUpperCase();
}

/** Flyout of one tool group. */
interface FlyoutProps {
  /** Group. */
  group: ToolGroup;
  /** Tools of the group. */
  tools: readonly ToolPlugin[];
  /** Armed tool. */
  activeTool: string | null;
  /** Arms a tool. */
  onPick: (tool: ToolPlugin) => void;
}

/**
 * Chevron button that opens a group's tool list beside the toolbar.
 * @param props Props.
 * @return Flyout trigger and panel.
 */
function GroupFlyout({
  group,
  tools,
  activeTool,
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
      if (
        event instanceof PointerEvent &&
        (panel.current?.contains(event.target as Node) ||
          trigger.current?.contains(event.target as Node))
      )
        return;
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
        className="drawbar__more"
        aria-label={`More ${GROUP_NAMES[group].toLowerCase()} tools`}
        aria-haspopup="menu"
        aria-expanded={position !== null}
        data-testid={`tool-group-${group}`}
        onClick={() => {
          const rect = trigger.current?.getBoundingClientRect();
          setPosition(
            position || !rect ? null : {left: rect.right + 6, top: rect.top - 4}
          );
        }}
      >
        <ChevronRight size={10} aria-hidden="true" />
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
            <button
              key={tool.manifest.id}
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
          ))}
        </div>
      )}
    </>
  );
}

/**
 * Drawing toolbar.
 * @return Toolbar.
 */
export function DrawingToolbar(): ReactElement {
  const runtime = useRuntime();
  const tools = useTools();
  const activeTool = useStore(runtime.drawing.activeTool);
  const magnet = useStore(runtime.drawing.magnet);
  useStore(runtime.drawing.revision);
  const selection = useStore(runtime.selection);
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
      return parsed && typeof parsed === 'object'
        ? (parsed as Record<string, string>)
        : {};
    } catch {
      return {};
    }
  });
  const pick = (tool: ToolPlugin) => {
    const next = {...chosen, [tool.manifest.group]: tool.manifest.id};
    setChosen(next);
    runtime.storage.setItem(GROUP_STORAGE_KEY, JSON.stringify(next));
    run(`drawing.tool.${tool.manifest.id}`);
  };
  return (
    <nav className="drawbar" aria-label="Drawing tools">
      <button
        type="button"
        className="drawbar__btn"
        aria-label="Pointer"
        title="Pointer (Esc)"
        aria-pressed={activeTool === null}
        onClick={() => run('drawing.pointer')}
      >
        <MousePointer2 size={18} aria-hidden="true" />
      </button>
      {groupTools(tools).map(({group, tools: members}) => {
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
                onClick={() => pick(tool)}
              >
                <Icon name={tool.manifest.icon} />
              </button>
              {members.length > 1 && (
                <GroupFlyout
                  group={group}
                  tools={members}
                  activeTool={activeTool}
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
        onClick={() => run('drawing.undo')}
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
        onClick={() => run('drawing.redo')}
      >
        <Redo2 size={18} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="drawbar__btn"
        aria-label="Magnet snapping to candles"
        title="Magnet: snap to open, high, low or close (hold Ctrl to invert)"
        aria-pressed={magnet}
        onClick={() => run('drawing.magnet')}
      >
        <Magnet size={18} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="drawbar__btn"
        aria-label={allLocked ? 'Unlock all drawings' : 'Lock all drawings'}
        aria-pressed={allLocked}
        disabled={drawings.length === 0}
        onClick={() => setAll({locked: !allLocked})}
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
        onClick={() => setAll({visible: allHidden})}
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
