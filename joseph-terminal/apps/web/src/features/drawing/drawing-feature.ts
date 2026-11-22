/**
 * @fileoverview Drawing features: history/selection commands and one
 * removable feature per drawing tool.
 */
import type {ToolPlugin} from '@joseph/drawing-core';
import {FIRST_PARTY_TOOLS} from '@joseph/drawing-tools';
import type {TerminalRuntime} from '../../composition/runtime';
import type {WebFeature} from '../../shell/react-bindings';
import {bindDefaultHotkeys} from '../shell/shell-features';
import {shellManifest} from '../shell/manifest';

/**
 * Returns the active chart's drawing controller.
 * @param runtime Runtime.
 * @return Controller or undefined.
 */
function activeController(runtime: TerminalRuntime) {
  return runtime.drawing.controllers.get(runtime.selection.get().chartId);
}

/**
 * Core drawing feature (undo/redo, cancel, delete, magnet).
 * @param runtime Runtime.
 * @return Feature.
 */
export function drawingFeature(runtime: TerminalRuntime): WebFeature {
  return {
    manifest: shellManifest(
      'drawing',
      'Drawing',
      'Drawing history, selection and snapping.',
      {
        uiSlots: ['drawing-toolbar'],
        removal: {
          notes:
            'Saved drawings stay in storage and reload when the feature returns.',
        },
      }
    ),
    activate(ctx) {
      const bump = () => runtime.drawing.revision.update(n => n + 1);
      const store = () =>
        runtime.drawing.stores.get(runtime.selection.get().chartId);
      const commands = [
        {
          id: 'drawing.undo',
          title: 'Undo drawing change',
          run: () => {
            store()?.undo();
            bump();
          },
        },
        {
          id: 'drawing.redo',
          title: 'Redo drawing change',
          run: () => {
            store()?.redo();
            bump();
          },
        },
        {
          id: 'drawing.cancel',
          title: 'Cancel drawing or clear selection',
          run: () => {
            activeController(runtime)?.handleKey('Escape');
          },
        },
        {
          id: 'drawing.delete',
          title: 'Delete selected drawing',
          run: () => {
            activeController(runtime)?.handleKey('Delete');
            bump();
          },
        },
        {
          id: 'drawing.magnet',
          title: 'Toggle magnet snapping to candles',
          run: () => {
            runtime.drawing.magnet.update(on => !on);
          },
        },
        {
          id: 'drawing.pointer',
          title: 'Use the pointer',
          run: () => {
            for (const controller of runtime.drawing.controllers.values())
              controller.setTool(null);
            runtime.drawing.activeTool.set(null);
          },
        },
      ];
      for (const command of commands) ctx.commands.register(command);
      bindDefaultHotkeys(
        (combo, id) => ctx.hotkeys.bind(combo, id),
        commands.map(c => c.id)
      );
      return () => undefined;
    },
  };
}

/**
 * Wraps a drawing tool as a removable feature.
 * @param runtime Runtime.
 * @param tool Tool plugin.
 * @return Feature.
 */
export function toolFeature(
  runtime: TerminalRuntime,
  tool: ToolPlugin
): WebFeature {
  const id = `drawing.tool.${tool.manifest.id}`;
  return {
    manifest: shellManifest(
      id,
      tool.manifest.displayName,
      `${tool.manifest.displayName} drawing tool.`,
      {
        uiSlots: ['drawing-toolbar'],
        dependencies: ['drawing'],
        removal: {
          notes: `Saved ${tool.manifest.displayName.toLowerCase()} drawings are kept and shown as unsupported placeholders until the tool returns.`,
        },
      }
    ),
    activate(ctx) {
      const unregister = runtime.tools.register(tool);
      ctx.commands.register({
        id,
        title: `Draw ${tool.manifest.displayName.toLowerCase()}`,
        run: () => {
          runtime.drawing.activeTool.set(tool.manifest.id);
          activeController(runtime)?.setTool(tool.manifest.id);
        },
      });
      bindDefaultHotkeys(
        (combo, commandId) => ctx.hotkeys.bind(combo, commandId),
        [id]
      );
      return () => {
        if (runtime.drawing.activeTool.get() === tool.manifest.id) {
          runtime.drawing.activeTool.set(null);
        }
        unregister();
      };
    },
  };
}

/**
 * Features for every first-party tool.
 * @param runtime Runtime.
 * @return Features.
 */
export function toolFeatures(runtime: TerminalRuntime): WebFeature[] {
  return FIRST_PARTY_TOOLS.map(tool => toolFeature(runtime, tool));
}
