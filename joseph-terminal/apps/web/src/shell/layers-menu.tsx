/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Body of the top bar's Layers menu: one row per indicator
 * plugin the engine lists as a layer, in registration order, with a
 * sub-toggle per study pane the plugin declares and a settings button,
 * then the chart's volume, session break and data window options.
 */

'use client';
import type {ChartState, IndicatorPluginInfo} from '@joseph/contracts';
import {
  DELTA_LEGEND,
  DELTA_PLUGIN_ID,
  layerPlugins,
} from '@joseph/indicator-viz';
import {layerStyle, layerVisible, paneShown} from '@joseph/terminal-shell';
import {Settings2} from 'lucide-react';
import type {ReactElement} from 'react';
import {useIndicatorPlugins} from './indicator-plugins';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';

/**
 * Returns the test id of a pane sub-toggle.
 * @param pluginId Plugin id.
 * @param paneId Pane id from `display.panes`.
 * @return `delta-pane-toggle` for the volume delta pane, otherwise
 *     `study-pane-toggle-<plugin>-<pane>`.
 */
export function paneToggleTestId(pluginId: string, paneId: string): string {
  return pluginId === DELTA_PLUGIN_ID
    ? 'delta-pane-toggle'
    : `study-pane-toggle-${pluginId}-${paneId}`;
}

/**
 * Renders one plugin row: visibility, pane sub-toggles and settings.
 * @param props Chart and plugin descriptor.
 * @return Row element.
 */
function PluginRow({
  chart,
  info,
}: {
  chart: ChartState;
  info: IndicatorPluginInfo;
}): ReactElement {
  const runtime = useRuntime();
  const chartId = chart.chart_id;
  const pluginId = info.plugin_id;
  const visible = layerVisible(chart, pluginId, info.display.default_visible);
  const style = layerStyle(chart, pluginId);
  return (
    <div className="layers-menu__row">
      <label className="j-check layers-menu__item">
        <input
          type="checkbox"
          checked={visible}
          data-testid={`layer-toggle-${pluginId}`}
          onChange={() =>
            void runtime.dispatch({
              type: 'setIndicatorLayer',
              chartId,
              pluginId,
              visible: !visible,
            })
          }
        />
        {info.display_name}
      </label>
      {visible &&
        info.display.panes.map(pane => {
          const shown = paneShown(style, pane.id);
          return (
            <label
              key={pane.id}
              className="j-check layers-menu__sub"
              title={pluginId === DELTA_PLUGIN_ID ? DELTA_LEGEND : undefined}
            >
              <input
                type="checkbox"
                checked={shown}
                data-testid={paneToggleTestId(pluginId, pane.id)}
                onChange={() =>
                  void runtime.dispatch({
                    type: 'setIndicatorLayerConfig',
                    chartId,
                    pluginId,
                    patch: {
                      style: {
                        ...style,
                        panes: {...style.panes, [pane.id]: !shown},
                      },
                    },
                  })
                }
              />
              {pane.title} pane
            </label>
          );
        })}
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
        aria-label={`${info.display_name} settings`}
        data-testid={`indicator-settings-${pluginId}`}
        onClick={() => void runtime.indicatorSettings.set({chartId, pluginId})}
      >
        <Settings2 size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

/**
 * Renders the Layers menu body for a chart.
 * @param props Chart whose layers are edited.
 * @return Menu element.
 */
export function LayersMenu({chart}: {chart: ChartState}): ReactElement {
  const runtime = useRuntime();
  const plugins = useIndicatorPlugins();
  const dataWindow = useStore(runtime.dataWindow);
  const chartId = chart.chart_id;
  const run = (id: string) =>
    void runtime.commands.execute(id).catch(() => undefined);
  return (
    <div className="layers-menu">
      <p className="layers-menu__note">
        Indicators are computed by the Joseph engine and drawn as reported.
      </p>
      {plugins === null ? (
        <p className="layers-menu__note" role="status">
          Loading indicators…
        </p>
      ) : (
        layerPlugins(plugins).map(info => (
          <PluginRow key={info.plugin_id} chart={chart} info={info} />
        ))
      )}
      <div className="j-menu__sep" />
      <label className="j-check layers-menu__item">
        <input
          type="checkbox"
          checked={chart.show_volume}
          onChange={() =>
            void runtime.dispatch({type: 'toggleVolume', chartId})
          }
        />
        Volume
      </label>
      <label className="j-check layers-menu__item">
        <input
          type="checkbox"
          checked={chart.session_separators === true}
          data-testid="session-separators-toggle"
          onChange={() => void run('chart.sessionSeparators')}
        />
        Session breaks (UTC)
      </label>
      <label className="j-check layers-menu__item">
        <input
          type="checkbox"
          checked={dataWindow}
          data-testid="data-window-toggle"
          onChange={() => void run('chart.dataWindow')}
        />
        Data window
      </label>
      <button
        type="button"
        className="j-menu__item"
        onClick={() => void run('indicators.toggle-ended')}
      >
        Show or hide ended zones
      </button>
    </div>
  );
}
