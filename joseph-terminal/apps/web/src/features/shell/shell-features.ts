/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Shell features: workspace layouts and sync, theme and the
 * command palette.
 */

import type {LayoutId} from '@joseph/contracts';
import {DrawingRepository} from '@joseph/drawing-core';
import {
  DEFAULT_HOTKEYS,
  encodeShareLink,
  exportWorkspaceFile,
} from '@joseph/terminal-shell';
import type {TerminalRuntime} from '../../composition/runtime';
import type {WebFeature} from '../../shell/react-bindings';
import {shellManifest} from './manifest';

/** Layout labels. */
export const LAYOUT_LABELS: Readonly<Record<LayoutId, string>> = {
  '1': 'Single chart',
  '2h': 'Two charts side by side',
  '2v': 'Two charts stacked',
  '4': 'Four charts',
  '8': 'Eight charts',
};

/**
 * Binds every default hotkey that targets one of the given command ids.
 * @param bind Hotkey binder.
 * @param ids Command ids owned by the feature.
 */
export function bindDefaultHotkeys(
  bind: (combo: string, commandId: string) => void,
  ids: readonly string[]
): void {
  for (const [combo, commandId] of Object.entries(DEFAULT_HOTKEYS)) {
    if (ids.includes(commandId)) bind(combo, commandId);
  }
}

/**
 * Workspace feature: layouts, sync toggles, export and share.
 * @param runtime Terminal runtime.
 * @return Feature.
 */
export function workspaceFeature(runtime: TerminalRuntime): WebFeature {
  return {
    manifest: shellManifest(
      'shell.workspace',
      'Workspace',
      'Layouts, chart sync, export and share links.',
      {
        uiSlots: ['workspace'],
      }
    ),
    activate(ctx) {
      let nonce = 0;
      ctx.commands.register({
        id: 'shell.showPanel',
        title: 'Show a panel tab',
        run: args => {
          const request = args as {slot?: unknown; id?: unknown} | undefined;
          if (
            (request?.slot !== 'side-panel' &&
              request?.slot !== 'bottom-panel') ||
            typeof request.id !== 'string'
          ) {
            return;
          }
          nonce += 1;
          runtime.panelRequest.set({slot: request.slot, id: request.id, nonce});
        },
      });
      const ids: string[] = [];
      for (const layout of Object.keys(LAYOUT_LABELS) as LayoutId[]) {
        const id = `layout.${layout}`;
        ids.push(id);
        ctx.commands.register({
          id,
          title: `Show ${LAYOUT_LABELS[layout].toLowerCase()}`,
          run: () => void runtime.dispatch({type: 'setLayout', layout}),
        });
      }
      for (const key of [
        'crosshair',
        'instrument',
        'timeframe',
        'viewport',
      ] as const) {
        ctx.commands.register({
          id: `sync.${key}`,
          title: `Toggle ${key} sync across charts`,
          run: () =>
            void runtime.dispatch({
              type: 'setSync',
              key,
              value: !runtime.workspace.get().sync[key],
            }),
        });
      }
      ctx.commands.register({
        id: 'workspace.export',
        title: 'Export workspace as JSON (layout and drawings)',
        run: () => {
          const workspace = runtime.workspace.get();
          // Drawings of every instrument in this workspace, as saved.
          const drawings = new DrawingRepository(
            runtime.storage,
            workspace.workspace_id,
            runtime.tools
          ).exportAll();
          const text = exportWorkspaceFile(
            workspace,
            drawings.collections,
            Date.now()
          );
          const blob = new Blob([text], {type: 'application/json'});
          const url = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = url;
          link.download = 'joseph-workspace.json';
          link.click();
          URL.revokeObjectURL(url);
          const count = drawings.drawings;
          const instruments = drawings.collections.length;
          ctx.notifier.notify(
            count === 0
              ? 'Workspace exported (no drawings to include).'
              : `Workspace exported with ${count} drawing${count === 1 ? '' : 's'} on ${instruments} instrument${instruments === 1 ? '' : 's'}.${drawings.omitted > 0 ? ` ${drawings.omitted} beyond the file limits stayed in this browser.` : ''}`,
            'success'
          );
        },
      });
      ctx.commands.register({
        id: 'workspace.share',
        title: 'Copy workspace share link',
        run: async () => {
          const url = `${window.location.origin}${window.location.pathname}#ws=${encodeShareLink(runtime.workspace.get())}`;
          try {
            await navigator.clipboard.writeText(url);
            ctx.notifier.notify(
              'Share link copied. It contains layout and chart settings, not drawings or account data.',
              'success'
            );
          } catch {
            ctx.notifier.notify(
              'Copy failed. Allow clipboard access and try again.',
              'error'
            );
          }
        },
      });
      bindDefaultHotkeys((combo, id) => ctx.hotkeys.bind(combo, id), ids);
      return () => undefined;
    },
  };
}

/**
 * Theme feature.
 * @param runtime Terminal runtime.
 * @return Feature.
 */
export function themeFeature(runtime: TerminalRuntime): WebFeature {
  return {
    manifest: shellManifest(
      'shell.theme',
      'Theme',
      'Night and daylight themes.'
    ),
    activate(ctx) {
      ctx.commands.register({
        id: 'theme.toggle',
        title: 'Switch between night and daylight theme',
        run: () =>
          void runtime.theme.update(theme =>
            theme === 'dark' ? 'light' : 'dark'
          ),
      });
      return () => undefined;
    },
  };
}

/**
 * Command palette feature.
 * @param runtime Terminal runtime.
 * @return Feature.
 */
export function paletteFeature(runtime: TerminalRuntime): WebFeature {
  return {
    manifest: shellManifest(
      'shell.palette',
      'Command palette',
      'Search and run any command.'
    ),
    activate(ctx) {
      ctx.commands.register({
        id: 'palette.open',
        title: 'Open command palette',
        run: () => void runtime.palette.set({open: true, query: '', index: 0}),
      });
      bindDefaultHotkeys(
        (combo, id) => ctx.hotkeys.bind(combo, id),
        ['palette.open']
      );
      return () => undefined;
    },
  };
}
