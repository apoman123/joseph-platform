/**
 * @fileoverview Left vertical drawing toolbar built from the tool registry.
 */
'use client';
import type {ToolPlugin} from '@joseph/drawing-core';
import {
  Eye,
  EyeOff,
  Lock,
  LockOpen,
  Magnet,
  MousePointer2,
  Redo2,
  Undo2,
} from 'lucide-react';
import {useEffect, useState, type ReactElement} from 'react';
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
  let lastGroup = '';
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
      {tools.map(tool => {
        const separator = tool.manifest.group !== lastGroup;
        lastGroup = tool.manifest.group;
        return (
          <div key={tool.manifest.id} className="drawbar__group">
            {separator && <span className="drawbar__sep" aria-hidden="true" />}
            <button
              type="button"
              className="drawbar__btn"
              aria-label={tool.manifest.displayName}
              title={
                tool.manifest.hotkey
                  ? `${tool.manifest.displayName} (${tool.manifest.hotkey.replace('alt', 'Alt').replace('+', ' + ').toUpperCase()})`
                  : tool.manifest.displayName
              }
              aria-pressed={activeTool === tool.manifest.id}
              data-testid={`tool-${tool.manifest.id}`}
              onClick={() => run(`drawing.tool.${tool.manifest.id}`)}
            >
              <Icon name={tool.manifest.icon} />
            </button>
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
