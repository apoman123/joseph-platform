/**
 * @fileoverview Chart grid for 1/2/4/8 layouts plus the chart context menu.
 */
'use client';
import {
  gridSizesFor,
  resizeTracks,
  tracksToTemplate,
  visibleCharts,
  type GridSizes,
} from '@joseph/terminal-shell';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactElement,
} from 'react';
import {ChartPane, type PaneMenu} from './chart-pane';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {SlotInline} from './slot-host';
import {formatNumber} from './format';
import {Splitter} from './splitter';

/** Workspace panel key holding chart-grid track sizes per layout. */
export const GRID_PANEL_KEY = 'shell.grid';

/**
 * Chart grid.
 * @return Grid.
 */
export function ChartGrid(): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const charts = visibleCharts(workspace);
  const gridRef = useRef<HTMLDivElement>(null);
  const savedSizes = (
    workspace.panels as Record<string, unknown> | undefined
  )?.[GRID_PANEL_KEY] as Record<string, unknown> | undefined;
  const committed = gridSizesFor(
    workspace.layout,
    savedSizes?.[workspace.layout]
  );
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
      data-testid="chart-grid"
      data-layout={workspace.layout}
      style={{
        gridTemplateColumns: tracksToTemplate(sizes.cols),
        gridTemplateRows: tracksToTemplate(sizes.rows),
      }}
    >
      {boundaries(sizes.cols).map((at, index) => (
        <Splitter
          key={`col-${index}`}
          orientation="vertical"
          label={`Resize chart columns ${index + 1} and ${index + 2}`}
          value={at * 100}
          min={0}
          max={100}
          className="chart-grid__splitter"
          style={{left: `calc(${at * 100}% - 3px)`}}
          testId={`grid-splitter-col-${index}`}
          onDrag={delta => resize('cols', index, delta)}
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
          style={{top: `calc(${at * 100}% - 3px)`}}
          testId={`grid-splitter-row-${index}`}
          onDrag={delta => resize('rows', index, delta)}
          onCommit={finish}
        />
      ))}
      {charts.map((chart, index) => (
        <div
          key={chart.chart_id}
          className="chart-grid__cell"
          style={{position: 'relative'}}
        >
          <ChartPane
            chart={chart}
            active={
              chart.chart_id === workspace.active_chart_id ||
              (index === 0 &&
                charts.every(c => c.chart_id !== workspace.active_chart_id))
            }
            runtime={runtime}
            onMenu={onMenu}
          />
          {menu?.chartId === chart.chart_id && (
            <div
              className="j-menu chart-menu"
              role="menu"
              aria-label="Chart actions"
              style={{left: menu.menu.x, top: menu.menu.y + 40}}
              onPointerDown={event => event.stopPropagation()}
            >
              <p className="chart-menu__price j-tabular">
                {menu.menu.price === null
                  ? 'No price here'
                  : `Price ${formatNumber(menu.menu.price, 2)}`}
              </p>
              <button
                type="button"
                role="menuitem"
                className="j-menu__item"
                onClick={() => {
                  if (menu.menu.price !== null)
                    void navigator.clipboard
                      ?.writeText(String(menu.menu.price))
                      .catch(() => undefined);
                  setMenu(null);
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
                  setMenu(null);
                }}
              >
                Draw a horizontal line
              </button>
              <SlotInline slot="chart-context-menu" />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
