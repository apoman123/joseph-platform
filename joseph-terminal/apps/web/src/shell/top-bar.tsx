/**
 * @fileoverview Top bar: brand, instrument, timeframes, chart style,
 * indicator layers, layout and sync, provenance, order mode, theme and the
 * command palette.
 */
'use client';
import type {ReactElement} from 'react';
import {
  TIMEFRAMES,
  type ChartType,
  type FeedState,
  type LayoutId,
  type PriceScaleMode,
} from '@joseph/contracts';
import {PLUGIN_NAMES} from '@joseph/indicator-viz';
import {activeChart} from '@joseph/terminal-shell';
import {
  Columns2,
  Command,
  Grid2x2,
  Layers,
  LayoutGrid,
  Moon,
  Rows2,
  Square,
  Star,
  Sun,
} from 'lucide-react';
import {InstrumentSelector} from './instrument-selector';
import {Popover} from './popover';
import {ProvenanceBadge} from './provenance';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {SlotInline} from './slot-host';

/** Chart type labels. */
const CHART_TYPES: readonly [ChartType, string][] = [
  ['candles', 'Candles'],
  ['bars', 'Bars'],
  ['line', 'Line'],
  ['area', 'Area'],
  ['heikin_ashi', 'Heikin Ashi'],
];

/** Price scale labels. */
const SCALES: readonly [PriceScaleMode, string][] = [
  ['linear', 'Linear'],
  ['log', 'Logarithmic'],
  ['percent', 'Percent'],
];

/** Layout options. */
const LAYOUTS: readonly [LayoutId, string, typeof Square][] = [
  ['1', 'One chart', Square],
  ['2h', 'Two charts side by side', Columns2],
  ['2v', 'Two charts stacked', Rows2],
  ['4', 'Four charts', Grid2x2],
  ['8', 'Eight charts', LayoutGrid],
];

/** Severity order used to summarize provenance across panes. */
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
 * Summarizes the worst provenance across visible panes.
 * @param states Feed state per chart.
 * @return Worst state.
 */
export function worstFeedState(
  states: Readonly<Record<string, FeedState>>
): FeedState {
  const values = Object.values(states);
  for (const state of SEVERITY) if (values.includes(state)) return state;
  return 'CONNECTING';
}

/**
 * Top bar component.
 * @return Header.
 */
export function TopBar(): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const theme = useStore(runtime.theme);
  const orderMode = useStore(runtime.orderMode);
  const feedStates = useStore(runtime.feedStates);
  const chart = activeChart(workspace);
  const favorites = workspace.favorites?.timeframes ?? [];
  const chartId = chart.chart_id;
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
      <InstrumentSelector
        instrumentId={chart.instrument_id}
        onSelect={instrumentId =>
          runtime.dispatch({type: 'setInstrument', chartId, instrumentId})
        }
      />
      <nav className="timeframes" aria-label="Timeframe">
        {favorites.map(tf => (
          <button
            key={tf}
            type="button"
            className="timeframes__item"
            aria-pressed={chart.timeframe === tf}
            onClick={() =>
              runtime.dispatch({type: 'setTimeframe', chartId, timeframe: tf})
            }
          >
            {tf}
          </button>
        ))}
        <Popover
          ariaLabel="All timeframes"
          label={
            <span>
              {favorites.includes(chart.timeframe) ? 'More' : chart.timeframe}
            </span>
          }
          className="timeframes__item timeframes__more"
        >
          {close => (
            <div className="tf-grid">
              {TIMEFRAMES.map(tf => (
                <div key={tf} className="tf-grid__row">
                  <button
                    type="button"
                    className="j-menu__item"
                    aria-pressed={chart.timeframe === tf}
                    onClick={() => {
                      runtime.dispatch({
                        type: 'setTimeframe',
                        chartId,
                        timeframe: tf,
                      });
                      close();
                    }}
                  >
                    {tf}
                  </button>
                  <button
                    type="button"
                    className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                    aria-label={
                      favorites.includes(tf)
                        ? `Remove ${tf} from favorites`
                        : `Add ${tf} to favorites`
                    }
                    aria-pressed={favorites.includes(tf)}
                    onClick={() =>
                      runtime.dispatch({
                        type: 'toggleFavoriteTimeframe',
                        timeframe: tf,
                      })
                    }
                  >
                    <Star
                      size={13}
                      fill={favorites.includes(tf) ? 'currentColor' : 'none'}
                      aria-hidden="true"
                    />
                  </button>
                </div>
              ))}
            </div>
          )}
        </Popover>
      </nav>
      <span className="topbar__divider" aria-hidden="true" />
      <label className="sr-only" htmlFor="chart-type">
        Chart style
      </label>
      <select
        id="chart-type"
        className="j-select topbar__select"
        value={chart.chart_type}
        onChange={e =>
          runtime.dispatch({
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
        onChange={e =>
          runtime.dispatch({
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
      <Popover
        ariaLabel="Indicator layers"
        testId="indicators-menu"
        label={
          <>
            <Layers size={16} aria-hidden="true" />
            <span>Layers</span>
          </>
        }
      >
        {() => (
          <div className="layers-menu">
            <p className="layers-menu__note">
              Zones and events are computed by the Joseph engine and drawn as
              reported.
            </p>
            {Object.entries(PLUGIN_NAMES).map(([pluginId, name]) => {
              const visible =
                chart.indicator_layers.find(l => l.plugin_id === pluginId)
                  ?.visible ?? false;
              return (
                <label key={pluginId} className="j-check layers-menu__item">
                  <input
                    type="checkbox"
                    checked={visible}
                    onChange={() =>
                      runtime.dispatch({
                        type: 'setIndicatorLayer',
                        chartId,
                        pluginId,
                        visible: !visible,
                      })
                    }
                  />
                  {name}
                </label>
              );
            })}
            <div className="j-menu__sep" />
            <label className="j-check layers-menu__item">
              <input
                type="checkbox"
                checked={chart.show_volume}
                onChange={() =>
                  runtime.dispatch({type: 'toggleVolume', chartId})
                }
              />
              Volume
            </label>
            <button
              type="button"
              className="j-menu__item"
              onClick={() => run('indicators.toggle-ended')}
            >
              Show or hide ended zones
            </button>
          </div>
        )}
      </Popover>
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
                    runtime.dispatch({
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
      <div className="topbar__spacer" />
      <SlotInline slot="toolbar" />
      <ProvenanceBadge state={worstFeedState(feedStates)} />
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
        className="j-btn j-btn--ghost palette-trigger"
        aria-label="Open command palette"
        data-testid="palette-trigger"
        onClick={() => run('palette.open')}
      >
        <Command size={14} aria-hidden="true" />
        <kbd>K</kbd>
      </button>
    </header>
  );
}
