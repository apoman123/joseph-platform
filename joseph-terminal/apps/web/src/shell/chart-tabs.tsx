/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Scrollable saved chart tabs with keyboard navigation. */

'use client';
import {parseInstrumentId, VENUE_NAMES} from '@joseph/contracts';
import {Plus, X} from 'lucide-react';
import {useEffect, useRef, useState, type ReactElement} from 'react';
import {ids} from './ids';
import {ScriptDialog} from './script-dialog';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';

/**
 * Renders saved chart tabs; only the selected layout's panes are mounted.
 * @return Tab strip with add, select and close controls.
 */
export function ChartTabs(): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const [adding, setAdding] = useState(false);
  const active = useRef<HTMLButtonElement>(null);
  const tabs = workspace.charts.map(chart => {
    const instrument = parseInstrumentId(chart.instrument_id);
    return {
      chart,
      label:
        chart.view_type === 'stock_heatmap'
          ? 'Stock heatmap'
          : `${instrument.venueMarketId} · ${chart.timeframe}`,
      venue: VENUE_NAMES[instrument.venue],
    };
  });
  useEffect(() => {
    active.current?.scrollIntoView?.({block: 'nearest', inline: 'nearest'});
    if (document.activeElement === document.body) active.current?.focus();
  }, [workspace.active_chart_id]);
  return (
    <div className="chart-tabs">
      <div
        className="chart-tabs__scroll"
        style={{gridTemplateColumns: `repeat(${tabs.length}, max-content)`}}
      >
        <div
          className="chart-tabs__list"
          role="tablist"
          aria-label="Chart tabs"
        >
          {tabs.map(({chart, label, venue}, index) => {
            const selected = chart.chart_id === workspace.active_chart_id;
            return (
              <button
                key={chart.chart_id}
                ref={selected ? active : undefined}
                id={`chart-tab-${chart.chart_id}`}
                type="button"
                role="tab"
                className={`chart-tabs__item ${selected ? 'chart-tabs__item--active' : ''}`}
                style={{gridColumn: index + 1, gridRow: 1}}
                aria-selected={selected}
                aria-controls="chart-tab-panel"
                tabIndex={selected ? 0 : -1}
                title={`${label} · ${venue}`}
                onClick={() =>
                  void runtime.dispatch({
                    type: 'setActiveChart',
                    chartId: chart.chart_id,
                  })
                }
                onKeyDown={event => {
                  const count = tabs.length;
                  let target: number;
                  if (event.key === 'ArrowRight') target = (index + 1) % count;
                  else if (event.key === 'ArrowLeft') {
                    target = (index - 1 + count) % count;
                  } else if (event.key === 'Home') target = 0;
                  else if (event.key === 'End') target = count - 1;
                  else if (event.key === 'Delete') {
                    event.preventDefault();
                    runtime.dispatch({
                      type: 'closeChart',
                      chartId: chart.chart_id,
                    });
                    return;
                  } else return;
                  event.preventDefault();
                  event.stopPropagation();
                  const next = tabs[target];
                  if (next) {
                    runtime.dispatch({
                      type: 'setActiveChart',
                      chartId: next.chart.chart_id,
                    });
                    document
                      .getElementById(`chart-tab-${next.chart.chart_id}`)
                      ?.focus();
                  }
                }}
              >
                <span>{label}</span>
              </button>
            );
          })}
        </div>
        {/* Close actions share grid cells but stay outside the ARIA tab list. */}
        {tabs.map(({chart, label}, index) => (
          <button
            key={chart.chart_id}
            type="button"
            className="chart-tabs__close"
            style={{gridColumn: index + 1, gridRow: 1}}
            aria-label={`Close ${label}`}
            title={`Close ${label}`}
            disabled={tabs.length === 1}
            onClick={() =>
              void runtime.dispatch({
                type: 'closeChart',
                chartId: chart.chart_id,
              })
            }
          >
            <X size={12} aria-hidden="true" />
          </button>
        ))}
      </div>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon chart-tabs__add"
        aria-label="Add chart tab"
        title="Choose a candlestick chart or stock heatmap"
        aria-haspopup="dialog"
        aria-expanded={adding}
        onClick={() => void setAdding(true)}
      >
        <Plus size={16} aria-hidden="true" />
      </button>
      {adding && (
        <ScriptDialog title="New tab" onClose={() => void setAdding(false)}>
          <p>Choose what to explore.</p>
          {(['candles', 'stock_heatmap'] as const).map(viewType => (
            <button
              key={viewType}
              type="button"
              className="j-btn"
              onClick={() => {
                runtime.dispatch({
                  type: 'addChart',
                  chartId: `chart-${ids.next()}`,
                  viewType,
                });
                setAdding(false);
              }}
            >
              {viewType === 'candles' ? 'Candlestick chart' : 'Stock heatmap'}
            </button>
          ))}
        </ScriptDialog>
      )}
    </div>
  );
}
