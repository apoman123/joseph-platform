/**
 * @fileoverview Chart grid for 1/2/4/8 layouts plus the chart context menu.
 */
'use client';
import {visibleCharts} from '@joseph/terminal-shell';
import {useCallback, useEffect, useState, type ReactElement} from 'react';
import {ChartPane, type PaneMenu} from './chart-pane';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {SlotInline} from './slot-host';
import {formatNumber} from './format';

/**
 * Chart grid.
 * @return Grid.
 */
export function ChartGrid(): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const charts = visibleCharts(workspace);
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
      className={`chart-grid chart-grid--${workspace.layout}`}
      data-testid="chart-grid"
      data-layout={workspace.layout}
    >
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
