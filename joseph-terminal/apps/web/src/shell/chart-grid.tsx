/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Chart grid for 1/2/4/8 layouts plus the chart context menu.
 *
 * Splitters between the tracks resize them by pointer or arrow keys (see
 * `Splitter`); sizes are saved per layout in the workspace. The context
 * menu is a `role="menu"` of `menuitem` buttons; Escape or any press
 * outside it closes it.
 */

'use client';
import {
  gridSizesFor,
  resizeTracks,
  tracksToTemplate,
  visibleCharts,
  type GridSizes,
} from '@joseph/terminal-shell';
import {parseInstrumentId, type ChartState} from '@joseph/contracts';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {ChartPane, type PaneMenu} from './chart-pane';
import {DetachedPlaceholder, PopOutButton, useDetached} from './detached';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {SlotInline} from './slot-host';
import {formatNumber} from './format';
import {Splitter} from './splitter';
import {StockHeatmap} from './stock-heatmap';
import {DrawingFavorites} from './drawing-favorites';

/** Workspace panel key holding chart-grid track sizes per layout. */
export const GRID_PANEL_KEY = 'shell.grid';

/**
 * Renders the chart context menu (copy price, horizontal line, feature
 * items) at the pointer.
 * @param props Menu request, runtime and close callback.
 * @return Menu element.
 */
export function ChartContextMenu({
  menu,
  runtime,
  onClose,
}: {
  menu: PaneMenu;
  runtime: TerminalRuntime;
  onClose: () => void;
}): ReactElement {
  return (
    <div
      className="j-menu chart-menu"
      role="menu"
      aria-label="Chart actions"
      // The menu point is relative to the canvas, which sits below the
      // 40px pane header inside the positioned grid cell.
      style={{left: menu.x, top: menu.y + 40}}
      onPointerDown={event => void event.stopPropagation()}
    >
      <p className="chart-menu__price j-tabular">
        {menu.price === null
          ? 'No price here'
          : `Price ${formatNumber(menu.price, 2)}`}
      </p>
      <button
        type="button"
        role="menuitem"
        className="j-menu__item"
        onClick={() => {
          if (menu.price !== null) {
            // The clipboard is missing outside secure contexts and may be
            // refused; copying is best effort.
            void navigator.clipboard
              ?.writeText(String(menu.price))
              .catch(() => undefined);
          }
          onClose();
        }}
      >
        Copy price
      </button>
      <button
        type="button"
        role="menuitem"
        className="j-menu__item"
        onClick={() => {
          void runtime.commands
            .execute('drawing.tool.horizontal-line')
            .catch(() => undefined);
          onClose();
        }}
      >
        Draw a horizontal line
      </button>
      <SlotInline slot="chart-context-menu" />
    </div>
  );
}

/**
 * Returns the title of a chart pane (window titles, placeholders).
 * @param chart Chart state.
 * @return Text such as `BTCUSD 5m`.
 */
export function chartTitle(chart: ChartState): string {
  return `${parseInstrumentId(chart.instrument_id).venueMarketId} ${chart.timeframe}`;
}

/**
 * Renders one grid cell: the chart pane, or a placeholder while it is
 * detached.
 * @param props Chart, whether it is active, runtime and the menu callback.
 * @return Pane or placeholder.
 */
function ChartSlot({
  chart,
  active,
  runtime,
  onMenu,
}: {
  chart: ChartState;
  active: boolean;
  runtime: TerminalRuntime;
  onMenu: (chartId: string, menu: PaneMenu | null) => void;
}): ReactElement {
  const ref = {kind: 'chart' as const, chartId: chart.chart_id};
  const detached = useDetached(ref);
  if (detached) {
    return (
      <DetachedPlaceholder pane={detached.pane} status={detached.status} />
    );
  }
  if (chart.view_type === 'stock_heatmap') {
    return <StockHeatmap chart={chart} active={active} runtime={runtime} />;
  }
  return (
    <ChartPane
      chart={chart}
      active={active}
      runtime={runtime}
      onMenu={onMenu}
      headerActions={
        <PopOutButton
          pane={ref}
          title={chartTitle(chart)}
          testId={`popout-${chart.chart_id}`}
        />
      }
    />
  );
}

/**
 * Renders the chart grid of the workspace layout.
 * @return Grid element.
 */
export function ChartGrid(): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const charts = visibleCharts(workspace);
  const gridRef = useRef<HTMLDivElement>(null);
  // Saved sizes are untrusted; `gridSizesFor` validates them and falls back
  // to equal tracks.
  const savedSizes = (
    workspace.panels as Record<string, unknown> | undefined
  )?.[GRID_PANEL_KEY] as Record<string, unknown> | undefined;
  const committed = gridSizesFor(
    workspace.layout,
    savedSizes?.[workspace.layout]
  );
  // Sizes while a splitter moves; the workspace is written once on commit.
  const [dragging, setDragging] = useState<GridSizes | null>(null);
  const draggingRef = useRef<GridSizes | null>(null);
  const sizes = dragging ?? committed;
  const commit = (next: GridSizes) => {
    runtime.dispatch({
      type: 'setPanel',
      featureId: GRID_PANEL_KEY,
      state: {...(savedSizes ?? {}), [workspace.layout]: next},
    });
    setDragging(null);
  };
  const resize = (axis: 'cols' | 'rows', index: number, deltaPx: number) => {
    const box = gridRef.current?.getBoundingClientRect();
    const extent = axis === 'cols' ? box?.width : box?.height;
    if (!extent) return;
    const base = draggingRef.current ?? committed;
    // Track sizes are fractions of the grid, so pixels are scaled by it.
    const next = {
      ...base,
      [axis]: resizeTracks(base[axis], index, deltaPx / extent),
    };
    draggingRef.current = next;
    setDragging(next);
  };
  const finish = () => {
    const value = draggingRef.current;
    draggingRef.current = null;
    if (value) commit(value);
    else setDragging(null);
  };
  // Positions of the inner track edges, as fractions of the grid.
  const boundaries = (tracks: readonly number[]) =>
    tracks
      .slice(0, -1)
      .map((_, i) => tracks.slice(0, i + 1).reduce((sum, t) => sum + t, 0));
  const [menu, setMenu] = useState<{chartId: string; menu: PaneMenu} | null>(
    null
  );
  const onMenu = useCallback((chartId: string, next: PaneMenu | null) => {
    setMenu(next ? {chartId, menu: next} : null);
  }, []);
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
  return (
    <div
      ref={gridRef}
      className={`chart-grid chart-grid--${workspace.layout}`}
      id="chart-tab-panel"
      role="tabpanel"
      aria-labelledby={`chart-tab-${workspace.active_chart_id}`}
      data-testid="chart-grid"
      data-layout={workspace.layout}
      style={{
        gridTemplateColumns: tracksToTemplate(sizes.cols),
        gridTemplateRows: tracksToTemplate(sizes.rows),
      }}
    >
      <DrawingFavorites />
      {boundaries(sizes.cols).map((at, index) => (
        <Splitter
          key={`col-${index}`}
          orientation="vertical"
          label={`Resize chart columns ${index + 1} and ${index + 2}`}
          value={at * 100}
          min={0}
          max={100}
          className="chart-grid__splitter"
          // Centres the 6px handle on the track edge.
          style={{left: `calc(${at * 100}% - 3px)`}}
          testId={`grid-splitter-col-${index}`}
          onDrag={delta => void resize('cols', index, delta)}
          onCommit={finish}
        />
      ))}
      {boundaries(sizes.rows).map((at, index) => (
        <Splitter
          key={`row-${index}`}
          orientation="horizontal"
          label={`Resize chart rows ${index + 1} and ${index + 2}`}
          value={at * 100}
          min={0}
          max={100}
          className="chart-grid__splitter"
          // Centres the 6px handle on the track edge.
          style={{top: `calc(${at * 100}% - 3px)`}}
          testId={`grid-splitter-row-${index}`}
          onDrag={delta => void resize('rows', index, delta)}
          onCommit={finish}
        />
      ))}
      {charts.map((chart, index) => (
        <div
          key={chart.chart_id}
          className="chart-grid__cell"
          style={{position: 'relative'}}
        >
          <ChartSlot
            chart={chart}
            // The first visible chart stands in for an active chart that
            // the layout does not show.
            active={
              chart.chart_id === workspace.active_chart_id ||
              (index === 0 &&
                charts.every(c => c.chart_id !== workspace.active_chart_id))
            }
            runtime={runtime}
            onMenu={onMenu}
          />
          {menu?.chartId === chart.chart_id && (
            <ChartContextMenu
              menu={menu.menu}
              runtime={runtime}
              onClose={() => void setMenu(null)}
            />
          )}
        </div>
      ))}
    </div>
  );
}
