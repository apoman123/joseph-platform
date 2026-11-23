/**
 * @fileoverview Terminal application shell (client only).
 */
'use client';
import {
  POPOUT_CHANNEL,
  broadcastChannelPort,
  decodeShareLink,
} from '@joseph/terminal-shell';
import {useEffect, useState, type ReactElement} from 'react';
import {startMainDetach} from '../composition/detach';
import {composeFeatures} from '../composition/features';
import {createRuntime, type TerminalRuntime} from '../composition/runtime';
import {ChartGrid} from './chart-grid';
import {CommandPalette, StatusBar, Toasts} from './chrome';
import {DetachedBanner} from './detached';
import {DrawingToolbar} from './drawing-toolbar';
import {IndicatorSettingsDialog} from './indicator-settings-dialog';
import {ids} from './ids';
import {useStore} from './react-bindings';
import {RuntimeProvider} from './runtime-context';
import {SlotTabs} from './slot-host';
import {TopBar} from './top-bar';

/**
 * Applies a shared workspace from `#ws=` in the URL, if present.
 * @param runtime Runtime.
 */
function importShareLink(runtime: TerminalRuntime): void {
  const match = /^#ws=([A-Za-z0-9_-]+)$/.exec(window.location.hash);
  if (!match?.[1]) return;
  const result = decodeShareLink(match[1], Date.now());
  history.replaceState(null, '', window.location.pathname);
  if (!result.ok) {
    runtime.notifier.notify(
      `The shared workspace couldn’t be opened: ${result.error}`,
      'error'
    );
    return;
  }
  runtime.dispatch({
    type: 'replace',
    workspace: {
      ...result.workspace,
      workspace_id: runtime.workspace.get().workspace_id,
    },
  });
  runtime.notifier.notify(
    'Opened the shared workspace. Your drawings were not changed.',
    'success'
  );
}

/**
 * Root terminal component.
 * @return Application shell.
 */
export function TerminalApp(): ReactElement {
  const [runtime] = useState(() => createRuntime(composeFeatures));
  const theme = useStore(runtime.theme);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void runtime.registry.activateAll().then(() => {
      if (!cancelled) setReady(true);
    });
    const offCaps = runtime.capabilities.subscribe(
      () => void runtime.registry.refresh()
    );
    return () => {
      cancelled = true;
      offCaps();
      runtime.registry.deactivateAll();
    };
  }, [runtime]);

  useEffect(() => {
    importShareLink(runtime);
    const onKey = (event: KeyboardEvent) => {
      if (runtime.hotkeys.handle(event)) event.preventDefault();
    };
    const onUnload = () => runtime.flush();
    window.addEventListener('keydown', onKey);
    window.addEventListener('beforeunload', onUnload);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('beforeunload', onUnload);
      runtime.flush();
    };
  }, [runtime]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  // Detachable panes: pop-out windows linked over BroadcastChannel. They
  // keep running across a reload of this window and reconnect by themselves.
  useEffect(() => {
    const controller = startMainDetach(runtime, {
      port: broadcastChannelPort(POPOUT_CHANNEL),
      open: (url, name, features) => window.open(url, name, features),
      now: () => Date.now(),
      windowId: `main-${ids.next()}`,
    });
    runtime.windows.set(controller.actions);
    // Tell pop-outs the main window is going (a reload usually), so they
    // keep their changes for the next main window.
    const onHide = () => controller.goodbye();
    window.addEventListener('pagehide', onHide);
    return () => {
      window.removeEventListener('pagehide', onHide);
      runtime.windows.set(null);
      controller.dispose();
    };
  }, [runtime]);

  return (
    <RuntimeProvider runtime={runtime}>
      <div
        className="terminal"
        data-demo={runtime.clients.demo}
        data-ready={ready}
      >
        <TopBar />
        <div className="terminal__body">
          <DrawingToolbar />
          <main className="terminal__charts" aria-label="Charts">
            <DetachedBanner />
            <ChartGrid />
          </main>
          <SlotTabs slot="side-panel" label="Side panel" orientation="side" />
          <SlotTabs
            slot="bottom-panel"
            label="Bottom panel"
            orientation="bottom"
          />
        </div>
        <StatusBar />
        <CommandPalette />
        <IndicatorSettingsDialog />
        <Toasts />
      </div>
    </RuntimeProvider>
  );
}
