/**
 * @fileoverview Pop-out window (`/popout?pane=…`): one chart pane or one
 * dock panel in its own browser window.
 *
 * The window runs its own runtime and provider connection in the same mode
 * as the main window (DEMO or live engine) and the same features, so the
 * same trading guards apply: orders are refused while its chart replays,
 * LIVE stays off unless the execution engine grants it, and the order mode
 * starts at Paper (it is never copied from another window). The workspace,
 * link groups, theme and crosshair follow the main window over
 * `BroadcastChannel`; closing the window docks the pane again.
 */
'use client';
import {
  POPOUT_CHANNEL,
  activeChart,
  broadcastChannelPort,
  paneKey,
  parsePopoutQuery,
  type PopoutTarget,
} from '@joseph/terminal-shell';
import {History, Moon, PanelTopClose, Sun} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from 'react';
import {
  startPopoutLink,
  type MainLinkState,
  type PopoutLink,
} from '../composition/detach';
import {readConfig} from '../composition/env';
import {composeFeatures} from '../composition/features';
import {createRuntime, type TerminalRuntime} from '../composition/runtime';
import {ChartContextMenu, chartTitle} from './chart-grid';
import {ChartPane, type PaneMenu} from './chart-pane';
import {CommandPalette, StatusBar, Toasts} from './chrome';
import {DrawingToolbar} from './drawing-toolbar';
import {ids} from './ids';
import {IndicatorSettingsDialog} from './indicator-settings-dialog';
import {InstrumentSelector} from './instrument-selector';
import {ProvenanceBadge} from './provenance';
import {useStore} from './react-bindings';
import {RuntimeProvider, useRuntime} from './runtime-context';
import {SlotPanelView, SlotTabs} from './slot-host';
import {TimeframeControl} from './timeframe-menu';

/** Titles of detachable panels. */
const PANEL_TITLES: Readonly<Record<string, string>> = {
  'market-info': 'Market',
  watchlist: 'Watchlist',
  screener: 'Screener',
  alerts: 'Alerts',
};

/** Link status wording. */
const LINK_TEXT: Readonly<Record<MainLinkState, string>> = {
  connecting: 'Connecting to the main window…',
  linked: 'Linked to the main window',
  lost: 'Main window not connected; changes stay in this window',
};

/**
 * Title of the pane a pop-out shows.
 * @param runtime Pop-out runtime.
 * @param target Pop-out target.
 * @return Title such as `BTCUSD 5m` or `Alerts`.
 */
function paneTitle(runtime: TerminalRuntime, target: PopoutTarget): string {
  const {ref} = target;
  if (ref.kind === 'panel') return PANEL_TITLES[ref.panelId] ?? ref.panelId;
  const chart = runtime.workspace
    .get()
    .charts.find(c => c.chart_id === ref.chartId);
  return chart ? chartTitle(chart) : 'Chart';
}

/**
 * Pop-out route entry: validates the link, then opens the window.
 * @return Window content.
 */
export function PopoutApp(): ReactElement {
  const [target] = useState(() => parsePopoutQuery(window.location.search));
  if (!target) {
    return (
      <main className="static-page">
        <h1>This window link is incomplete</h1>
        <p>
          Open panes in another window from the terminal: use the window button
          in a chart header or panel.
        </p>
        <a className="j-btn j-btn--primary" href="/">
          Open the terminal
        </a>
      </main>
    );
  }
  return <PopoutWindow target={target} />;
}

/**
 * Pop-out window bound to one pane.
 * @param props Target pane.
 * @return Window frame.
 */
function PopoutWindow({target}: {target: PopoutTarget}): ReactElement {
  const linkRef = useRef<PopoutLink | null>(null);
  const [runtime] = useState(() =>
    createRuntime(composeFeatures, readConfig(), {
      role: 'popout',
      workspaceId: target.workspaceId,
      pinnedChartId: target.ref.kind === 'chart' ? target.ref.chartId : null,
      onAction: action => linkRef.current?.forward(action),
    })
  );
  const [link, setLink] = useState<PopoutLink | null>(null);
  const [ready, setReady] = useState(false);
  const theme = useStore(runtime.theme);
  const workspace = useStore(runtime.workspace);

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
    const next = startPopoutLink(
      runtime,
      {
        port: broadcastChannelPort(POPOUT_CHANNEL),
        open: () => null,
        now: () => Date.now(),
        windowId: `popout-${ids.next()}`,
      },
      {
        ref: target.ref,
        title: () => paneTitle(runtime, target),
        hidden: () => document.visibilityState === 'hidden',
        close: () => window.close(),
        focus: () => window.focus(),
      }
    );
    linkRef.current = next;
    setLink(next);
    // `pagehide` (not `beforeunload`) also fires on mobile and bfcache.
    const onHide = (event: PageTransitionEvent) => {
      if (!event.persisted) next.goodbye();
    };
    const onKey = (event: KeyboardEvent) => {
      if (runtime.hotkeys.handle(event)) event.preventDefault();
    };
    window.addEventListener('pagehide', onHide);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('pagehide', onHide);
      window.removeEventListener('keydown', onKey);
      linkRef.current = null;
      next.dispose();
      setLink(null);
    };
  }, [runtime, target]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const title = paneTitle(runtime, target);
  useEffect(() => {
    document.title = `${title} · Joseph Terminal`;
  }, [title]);

  const chartId = target.ref.kind === 'chart' ? target.ref.chartId : null;
  const chartExists =
    chartId === null || workspace.charts.some(c => c.chart_id === chartId);
  return (
    <RuntimeProvider runtime={runtime}>
      <div
        className="terminal popout"
        data-demo={runtime.clients.demo}
        data-ready={ready}
        data-pane={paneKey(target.ref)}
        data-testid="popout"
      >
        <PopoutBar target={target} link={link} title={title} />
        {target.ref.kind === 'chart' ? (
          <div className="popout__body popout__body--chart">
            <DrawingToolbar />
            <main className="popout__main" aria-label="Chart">
              {chartExists && chartId ? (
                <PopoutChart chartId={chartId} />
              ) : (
                <MissingPane onClose={() => window.close()} />
              )}
            </main>
            <SlotTabs
              slot="side-panel"
              label="Side panel"
              orientation="side"
              storagePrefix="joseph.popout.panel"
              defaultCollapsed
              detachable={false}
            />
          </div>
        ) : (
          <div className="popout__body">
            <main
              className="popout__main popout__main--panel"
              aria-label={title}
            >
              <SlotPanelView slot={target.ref.slot} id={target.ref.panelId} />
            </main>
          </div>
        )}
        <StatusBar />
        <CommandPalette />
        <IndicatorSettingsDialog />
        <Toasts />
      </div>
    </RuntimeProvider>
  );
}

/**
 * Shown when the chart of a pop-out link is no longer in the workspace.
 * @param props Close callback.
 * @return Notice.
 */
function MissingPane({onClose}: {onClose: () => void}): ReactElement {
  return (
    <div className="j-empty popout__missing">
      <strong>This chart is no longer in the workspace</strong>
      <p>It was removed or the workspace was replaced in the main window.</p>
      <button type="button" className="j-btn" onClick={onClose}>
        Close window
      </button>
    </div>
  );
}

/**
 * The pop-out's chart pane with its context menu.
 * @param props Chart id.
 * @return Pane.
 */
function PopoutChart({chartId}: {chartId: string}): ReactElement | null {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const chart = workspace.charts.find(c => c.chart_id === chartId);
  const [menu, setMenu] = useState<PaneMenu | null>(null);
  const onMenu = useCallback(
    (_id: string, next: PaneMenu | null) => setMenu(next),
    []
  );
  useEffect(() => {
    if (!menu) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return;
      setMenu(null);
    };
    window.addEventListener('keydown', close);
    window.addEventListener('pointerdown', close);
    return () => {
      window.removeEventListener('keydown', close);
      window.removeEventListener('pointerdown', close);
    };
  }, [menu]);
  if (!chart) return null;
  return (
    <div className="popout__chart">
      <ChartPane chart={chart} active runtime={runtime} onMenu={onMenu} />
      {menu && (
        <ChartContextMenu
          menu={menu}
          runtime={runtime}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  );
}

/**
 * Compact window bar: link state, the pane's instrument and timeframe
 * (charts), provenance, order mode, theme and "Bring back".
 * @param props Target, link and title.
 * @return Header.
 */
function PopoutBar({
  target,
  link,
  title,
}: {
  target: PopoutTarget;
  link: PopoutLink | null;
  title: string;
}): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const theme = useStore(runtime.theme);
  const orderMode = useStore(runtime.orderMode);
  const feedStates = useStore(runtime.feedStates);
  const replayStates = useStore(runtime.replay);
  const linkState = useStore(link?.main ?? IDLE_LINK);
  const chart = target.ref.kind === 'chart' ? activeChart(workspace) : null;
  const run = (id: string) =>
    void runtime.commands.execute(id).catch(() => undefined);
  return (
    <header className="topbar popout__bar">
      <div className="brand" aria-label="Joseph Terminal">
        <span className="brand__mark" aria-hidden="true">
          J
        </span>
      </div>
      <span
        className="popout__link"
        data-state={linkState}
        title={LINK_TEXT[linkState]}
        data-testid="popout-link"
      >
        <span className="popout__link-dot" aria-hidden="true" />
        <span className="sr-only">{LINK_TEXT[linkState]}</span>
      </span>
      {chart ? (
        <>
          <InstrumentSelector
            instrumentId={chart.instrument_id}
            onSelect={instrumentId =>
              runtime.dispatch({
                type: 'setInstrument',
                chartId: chart.chart_id,
                instrumentId,
              })
            }
          />
          <TimeframeControl chart={chart} />
          <button
            type="button"
            className="j-btn j-btn--ghost j-btn--icon"
            aria-label="Bar replay"
            aria-pressed={replayStates[chart.chart_id] !== undefined}
            title="Bar replay (Alt + Shift + R)"
            onClick={() => run('chart.replay.toggle')}
          >
            <History size={16} aria-hidden="true" />
          </button>
        </>
      ) : (
        <strong className="popout__title">{title}</strong>
      )}
      <div className="topbar__spacer" />
      {chart && (
        <ProvenanceBadge state={feedStates[chart.chart_id] ?? 'CONNECTING'} />
      )}
      <span
        className="j-badge"
        title="Order mode of this window. It starts at Paper and is never copied from another window."
        data-testid="order-mode"
      >
        {orderMode === 'PAPER'
          ? 'Paper trading'
          : orderMode === 'SHADOW'
            ? 'Shadow mode'
            : 'Live trading'}
      </span>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon"
        aria-label={
          theme === 'dark'
            ? 'Switch to daylight theme'
            : 'Switch to night theme'
        }
        onClick={() => run('theme.toggle')}
      >
        {theme === 'dark' ? (
          <Sun size={16} aria-hidden="true" />
        ) : (
          <Moon size={16} aria-hidden="true" />
        )}
      </button>
      <button
        type="button"
        className="j-btn j-btn--sm"
        data-testid="popout-bring-back"
        title="Close this window and show the pane in the main window again"
        onClick={() => {
          if (link) link.bringBack();
          else window.close();
        }}
      >
        <PanelTopClose size={14} aria-hidden="true" />
        Bring back
      </button>
    </header>
  );
}

/** Link store used before the link starts. */
const IDLE_LINK = {
  get: (): MainLinkState => 'connecting',
  subscribe: () => () => undefined,
};
