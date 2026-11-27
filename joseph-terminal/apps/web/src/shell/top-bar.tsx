/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Top bar: brand, instrument, timeframes, chart style,
 * indicator layers, layout and sync, provenance, order mode, theme and the
 * command palette.
 *
 * Selects have visually hidden labels; icon-only buttons carry an
 * `aria-label` (replay and screenshot also name their hotkey in the
 * tooltip); toggles report their state through `aria-pressed`. Menus open
 * in popovers (see `Popover`).
 */

'use client';
import type {ReactElement} from 'react';
import {
  type ChartType,
  type FeedState,
  type LayoutId,
  type PriceScaleMode,
} from '@joseph/contracts';
import {activeChart} from '@joseph/terminal-shell';
import {
  Camera,
  Columns2,
  Command,
  GitCompareArrows,
  Grid2x2,
  History,
  LayoutTemplate,
  LayoutGrid,
  Moon,
  Rows2,
  Square,
  Sun,
} from 'lucide-react';
import {CompareMenu} from './compare-menu';
import {InstrumentSelector} from './instrument-selector';
import {IndicatorsButton} from './indicator-library';
import {Popover} from './popover';
import {ProvenanceBadge} from './provenance';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {SlotInline} from './slot-host';
import {TemplatesMenu} from './templates-menu';
import {TimeframeControl} from './timeframe-menu';

/** Chart types in menu order, with their labels. */
const CHART_TYPES: ReadonlyArray<[ChartType, string]> = [
  ['candles', 'Candles'],
  ['bars', 'Bars'],
  ['line', 'Line'],
  ['area', 'Area'],
  ['heikin_ashi', 'Heikin Ashi'],
];

/** Price scale modes in menu order, with their labels. */
const SCALES: ReadonlyArray<[PriceScaleMode, string]> = [
  ['linear', 'Linear'],
  ['log', 'Logarithmic'],
  ['percent', 'Percent'],
];

/** Layouts with their accessible names and icons. */
const LAYOUTS: ReadonlyArray<[LayoutId, string, typeof Square]> = [
  ['1', 'One chart', Square],
  ['2h', 'Two charts side by side', Columns2],
  ['2v', 'Two charts stacked', Rows2],
  ['4', 'Four charts', Grid2x2],
  ['8', 'Eight charts', LayoutGrid],
];

/** Feed states from worst to best, to summarize provenance across panes. */
const SEVERITY: readonly FeedState[] = [
  'UNAVAILABLE',
  'STALE',
  'CONNECTING',
  'REPLAY',
  'POLL',
  'DEMO',
  'LIVE',
];

/**
 * Returns the worst provenance across visible panes.
 * @param states Feed state per chart id.
 * @return Worst state; `CONNECTING` when no pane reported one.
 */
export function worstFeedState(
  states: Readonly<Record<string, FeedState>>
): FeedState {
  const values = Object.values(states);
  for (const state of SEVERITY) {
    if (values.includes(state)) return state;
  }
  return 'CONNECTING';
}

/**
 * Renders the top bar for the active chart.
 * @return Header element.
 */
export function TopBar(): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const theme = useStore(runtime.theme);
  const orderMode = useStore(runtime.orderMode);
  const feedStates = useStore(runtime.feedStates);
  const replayStates = useStore(runtime.replay);
  const chart = activeChart(workspace);
  const replaying = replayStates[chart.chart_id] !== undefined;
  const chartId = chart.chart_id;
  const heatmap = chart.view_type === 'stock_heatmap';
  const run = (id: string) =>
    void runtime.commands.execute(id).catch(() => undefined);
  return (
    <header className="topbar">
      <div className="brand" aria-label="Joseph Terminal">
        <span className="brand__mark" aria-hidden="true">
          J
        </span>
        <span className="brand__word">Joseph</span>
      </div>
      {heatmap ? (
        <strong>Stock heatmap</strong>
      ) : (
        <>
          <InstrumentSelector
            instrumentId={chart.instrument_id}
            onSelect={instrumentId =>
              void runtime.dispatch({
                type: 'setInstrument',
                chartId,
                instrumentId,
              })
            }
          />
          <TimeframeControl chart={chart} />
          <span className="topbar__divider" aria-hidden="true" />
          <label className="sr-only" htmlFor="chart-type">
            Chart style
          </label>
          <select
            id="chart-type"
            className="j-select topbar__select"
            value={chart.chart_type}
            // The options are exactly `CHART_TYPES`.
            onChange={e =>
              void runtime.dispatch({
                type: 'setChartType',
                chartId,
                chartType: e.target.value as ChartType,
              })
            }
          >
            {CHART_TYPES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <label className="sr-only" htmlFor="price-scale">
            Price scale
          </label>
          <select
            id="price-scale"
            className="j-select topbar__select topbar__select--narrow"
            value={chart.price_scale}
            // The options are exactly `SCALES`.
            onChange={e =>
              void runtime.dispatch({
                type: 'setPriceScale',
                chartId,
                priceScale: e.target.value as PriceScaleMode,
              })
            }
          >
            {SCALES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <IndicatorsButton chart={chart} />
        </>
      )}
      <Popover
        ariaLabel="Chart layout"
        testId="layout-menu"
        label={
          <>
            <LayoutGrid size={16} aria-hidden="true" />
            <span>Layout</span>
          </>
        }
      >
        {close => (
          <div className="layout-menu">
            <div className="layout-menu__grid" role="group" aria-label="Layout">
              {LAYOUTS.map(([layout, label, layoutIcon]) => {
                const LayoutIcon = layoutIcon;
                return (
                  <button
                    key={layout}
                    type="button"
                    className="j-btn j-btn--icon"
                    aria-label={label}
                    title={label}
                    aria-pressed={workspace.layout === layout}
                    data-testid={`layout-${layout}`}
                    onClick={() => {
                      runtime.dispatch({type: 'setLayout', layout});
                      close();
                    }}
                  >
                    <LayoutIcon size={16} aria-hidden="true" />
                  </button>
                );
              })}
            </div>
            <div className="j-menu__sep" />
            <p className="layers-menu__note">Keep charts in step</p>
            {(
              ['crosshair', 'instrument', 'timeframe', 'viewport'] as const
            ).map(key => (
              <label key={key} className="j-check layers-menu__item">
                <input
                  type="checkbox"
                  checked={workspace.sync[key]}
                  data-testid={`sync-${key}`}
                  onChange={() =>
                    void runtime.dispatch({
                      type: 'setSync',
                      key,
                      value: !workspace.sync[key],
                    })
                  }
                />
                Sync {key}
              </label>
            ))}
          </div>
        )}
      </Popover>
      {!heatmap && (
        <>
          <Popover
            ariaLabel="Compare symbols"
            triggerLabel="Compare symbols"
            testId="compare-menu"
            className="j-btn j-btn--ghost j-btn--icon"
            label={<GitCompareArrows size={16} aria-hidden="true" />}
          >
            {() => <CompareMenu chart={chart} />}
          </Popover>
          <Popover
            ariaLabel="Chart templates"
            triggerLabel="Chart templates"
            testId="templates-menu"
            label={<LayoutTemplate size={16} aria-hidden="true" />}
            className="j-btn j-btn--ghost j-btn--icon"
          >
            {close => <TemplatesMenu chart={chart} close={close} />}
          </Popover>
          <button
            type="button"
            className="j-btn j-btn--ghost j-btn--icon"
            aria-label="Bar replay"
            aria-pressed={replaying}
            title="Bar replay (Alt + Shift + R)"
            data-testid="replay-toggle"
            onClick={() => void run('chart.replay.toggle')}
          >
            <History size={16} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="j-btn j-btn--ghost j-btn--icon"
            aria-label="Download chart screenshot"
            title="Screenshot (Alt + Shift + S)"
            data-testid="screenshot"
            onClick={() => void run('chart.screenshot')}
          >
            <Camera size={16} aria-hidden="true" />
          </button>
        </>
      )}
      <div className="topbar__spacer" />
      {!heatmap && <SlotInline slot="toolbar" />}
      {/* With every chart detached, provenance shows in the pop-outs. */}
      {Object.keys(feedStates).length > 0 && (
        <ProvenanceBadge state={worstFeedState(feedStates)} />
      )}
      <span
        className={`j-badge ${orderMode === 'LIVE' ? 'j-badge--ask' : ''}`}
        title="Order mode. Live trading stays off until the execution engine reports it is available and you consent."
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
        data-testid="theme-toggle"
        onClick={() => void run('theme.toggle')}
      >
        {theme === 'dark' ? (
          <Sun size={16} aria-hidden="true" />
        ) : (
          <Moon size={16} aria-hidden="true" />
        )}
      </button>
      <button
        type="button"
        className="j-btn j-btn--ghost palette-trigger"
        aria-label="Open command palette"
        data-testid="palette-trigger"
        onClick={() => void run('palette.open')}
      >
        <Command size={14} aria-hidden="true" />
        <kbd>K</kbd>
      </button>
    </header>
  );
}
