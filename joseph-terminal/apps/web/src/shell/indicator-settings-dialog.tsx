/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Indicator settings dialog for one chart layer: inputs built
 * from the plugin's published `input_schema` (validated, then sent to the
 * engine with every query), per-kind visibility and colors, event markers,
 * line colors and the study pane toggles, all labelled from the plugin's
 * display metadata. Settings are saved in the workspace, so they travel
 * with templates, exports and share links.
 *
 * The dialog is modal (`aria-modal`, labelled by its title). The first
 * control takes focus on open; Escape, Cancel, the close button or a press
 * on the backdrop close it without saving. Text inputs point at their help
 * or error text with `aria-describedby` and set `aria-invalid` when
 * invalid.
 */

'use client';
import type {AoiKind, IndicatorPluginInfo} from '@joseph/contracts';
import {
  eventDrawn,
  eventLabel,
  eventStyle,
  kindLabel,
  panePlots,
} from '@joseph/indicator-viz';
import {
  layerSettings,
  layerStyle,
  layerVisible,
  paneShown,
  type IndicatorLayerStyle,
} from '@joseph/terminal-shell';
import {X} from 'lucide-react';
import {useEffect, useMemo, useRef, useState, type ReactElement} from 'react';
import {loadIndicatorPlugins} from './indicator-plugins';
import {
  collectOverrides,
  fieldLabel,
  fieldsFromSchema,
  initialValues,
} from './indicator-settings';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';

/** Color the pickers show for kinds, events and lines without a color. */
const DEFAULT_SWATCH = '#8fa5b3';

/**
 * Renders the indicator settings dialog while a request is open.
 * @return Dialog, or null without a request.
 */
export function IndicatorSettingsDialog(): ReactElement | null {
  const runtime = useRuntime();
  const request = useStore(runtime.indicatorSettings);
  if (!request) return null;
  return (
    <SettingsForm
      key={`${request.chartId}:${request.pluginId}`}
      chartId={request.chartId}
      pluginId={request.pluginId}
    />
  );
}

/**
 * Renders the settings form of one chart layer.
 * @param props Chart and plugin ids.
 * @return Dialog, or an empty fragment when the chart no longer exists.
 */
function SettingsForm({
  chartId,
  pluginId,
}: {
  chartId: string;
  pluginId: string;
}): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const chart = workspace.charts.find(c => c.chart_id === chartId);
  const [plugin, setPlugin] = useState<IndicatorPluginInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let alive = true;
    loadIndicatorPlugins(runtime).then(
      plugins => {
        if (!alive) return;
        const found = plugins.find(p => p.plugin_id === pluginId) ?? null;
        setPlugin(found);
        if (!found) setLoadError('The engine does not publish this indicator.');
      },
      (failure: unknown) => {
        if (alive) {
          setLoadError(
            failure instanceof Error ? failure.message : 'Request failed'
          );
        }
      }
    );
    return () => {
      alive = false;
    };
  }, [runtime, pluginId]);
  const fields = useMemo(
    () => fieldsFromSchema(plugin?.input_schema),
    [plugin]
  );
  const saved = chart ? layerSettings(chart, pluginId) : null;
  const style: IndicatorLayerStyle = chart ? layerStyle(chart, pluginId) : {};
  const layer = chart?.indicator_layers.find(l => l.plugin_id === pluginId);
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [kinds, setKinds] = useState<readonly AoiKind[] | null>(
    layer?.kinds ?? null
  );
  const [colors, setColors] = useState<Record<string, string>>({
    ...(style.colors ?? {}),
  });
  // Null follows the plugin's `display.show_events`.
  const [showEvents, setShowEvents] = useState<boolean | null>(
    style.show_events ?? null
  );
  // Pane visibility starts from the saved style; toggles override it.
  const [paneBase, setPaneBase] = useState<IndicatorLayerStyle>(style);
  const [paneOverrides, setPaneOverrides] = useState<Record<string, boolean>>(
    {}
  );
  useEffect(() => {
    setValues(initialValues(fields, saved));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs only when the schema arrives; later edits stay local until saved
  }, [fields]);
  const close = () => void runtime.indicatorSettings.set(null);
  useEffect(() => {
    dialog.current
      ?.querySelector<HTMLElement>('input, select, button')
      ?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKey);
    return () => void window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- focus and the Escape listener are set up once per dialog; `close` only clears a store
  }, []);
  if (!chart) return <></>;
  const outputKinds = plugin?.output_kinds ?? [];
  const drawnEvents = (plugin?.output_events ?? []).filter(name =>
    eventDrawn(eventStyle(plugin ?? undefined, name), name)
  );
  const lines = plugin ? panePlots(plugin, 'price') : [];
  const panes = plugin?.display.panes ?? [];
  const eventsOn = showEvents ?? plugin?.display.show_events ?? true;
  const shownKinds = kinds ?? outputKinds;
  const name = plugin?.display_name ?? pluginId;
  /**
   * Returns whether a pane is currently toggled on.
   * @param paneId Pane id.
   * @return Visibility.
   */
  function paneOn(paneId: string): boolean {
    return paneOverrides[paneId] ?? paneShown(paneBase, paneId);
  }
  const save = () => {
    const result = collectOverrides(fields, values);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    const allKinds =
      kinds === null || outputKinds.every(k => kinds.includes(k));
    // Without metadata the saved pane settings are kept as they were.
    const hiddenPane = panes.some(pane => !paneOn(pane.id));
    const paneStyle: IndicatorLayerStyle =
      panes.length === 0
        ? {
            ...(paneBase.pane !== undefined ? {pane: paneBase.pane} : {}),
            ...(paneBase.panes ? {panes: paneBase.panes} : {}),
          }
        : hiddenPane
          ? {panes: Object.fromEntries(panes.map(p => [p.id, paneOn(p.id)]))}
          : {};
    const nextStyle: IndicatorLayerStyle = {
      ...(Object.keys(colors).length > 0 ? {colors} : {}),
      ...(showEvents !== null ? {show_events: showEvents} : {}),
      ...paneStyle,
    };
    runtime.dispatch({
      type: 'setIndicatorLayerConfig',
      chartId,
      pluginId,
      patch: {
        kinds: allKinds ? null : [...(kinds ?? [])],
        style: nextStyle,
        settings:
          Object.keys(result.overrides).length > 0 ? result.overrides : null,
        visible: layerVisible(
          chart,
          pluginId,
          plugin?.display.default_visible ?? true
        ),
      },
    });
    runtime.notifier.notify(`${name} settings saved for this chart`, 'success');
    close();
  };
  const reset = () => {
    setValues(initialValues(fields, null));
    setErrors({});
    setKinds(null);
    setColors({});
    setShowEvents(null);
    setPaneBase({});
    setPaneOverrides({});
  };
  return (
    <div className="dialog-backdrop" onPointerDown={close}>
      <div
        ref={dialog}
        className="dialog indicator-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="indicator-dialog-title"
        data-testid="indicator-settings"
        onPointerDown={event => void event.stopPropagation()}
      >
        <header className="dialog__head">
          <h2 id="indicator-dialog-title">{name}</h2>
          <button
            type="button"
            className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
            aria-label="Close settings"
            onClick={close}
          >
            <X size={15} aria-hidden="true" />
          </button>
        </header>
        <form
          className="dialog__body"
          onSubmit={event => {
            event.preventDefault();
            save();
          }}
        >
          {loadError && (
            <p className="j-notice j-notice--warning" role="status">
              Inputs unavailable: {loadError}. Colors and visibility still
              apply.
            </p>
          )}
          {runtime.clients.demo && (
            <p className="j-notice" role="note">
              Demo studies follow their inputs; demo levels are precomputed, so
              their inputs are saved and sent but do not move them.
            </p>
          )}
          {fields.length > 0 && (
            <fieldset className="indicator-dialog__group">
              <legend>Inputs</legend>
              {fields.map(field => {
                const id = `input-${field.name}`;
                const error = errors[field.name];
                const value = values[field.name];
                return (
                  <div key={field.name} className="indicator-dialog__field">
                    {field.kind === 'boolean' ? (
                      <label className="j-check">
                        <input
                          id={id}
                          type="checkbox"
                          checked={value === true}
                          onChange={event =>
                            void setValues(v => ({
                              ...v,
                              [field.name]: event.target.checked,
                            }))
                          }
                        />
                        {fieldLabel(field.name)}
                      </label>
                    ) : (
                      <>
                        <label className="j-label" htmlFor={id}>
                          {fieldLabel(field.name)}
                        </label>
                        {field.kind === 'enum' ? (
                          <select
                            id={id}
                            className="j-select"
                            value={String(value ?? '')}
                            onChange={event =>
                              void setValues(v => ({
                                ...v,
                                [field.name]: event.target.value,
                              }))
                            }
                          >
                            {field.choices?.map(choice => (
                              <option key={choice} value={choice}>
                                {choice}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            id={id}
                            className="j-input j-tabular"
                            inputMode={
                              field.kind === 'integer' ? 'numeric' : 'decimal'
                            }
                            value={String(value ?? '')}
                            aria-invalid={error ? true : undefined}
                            aria-describedby={`${id}-help`}
                            onChange={event =>
                              void setValues(v => ({
                                ...v,
                                [field.name]: event.target.value,
                              }))
                            }
                          />
                        )}
                      </>
                    )}
                    <p
                      id={`${id}-help`}
                      className={
                        error
                          ? 'indicator-dialog__error'
                          : 'indicator-dialog__help'
                      }
                    >
                      {error ??
                        `${field.description} Default ${String(field.defaultValue)}.`}
                    </p>
                  </div>
                );
              })}
            </fieldset>
          )}
          {outputKinds.length > 0 && (
            <fieldset className="indicator-dialog__group">
              <legend>Zones and levels</legend>
              {outputKinds.map(kind => {
                const label = kindLabel(plugin ?? undefined, kind);
                return (
                  <div key={kind} className="indicator-dialog__kind">
                    <label className="j-check" title={kind}>
                      <input
                        type="checkbox"
                        checked={shownKinds.includes(kind)}
                        onChange={event =>
                          void setKinds(
                            event.target.checked
                              ? [...shownKinds, kind]
                              : shownKinds.filter(k => k !== kind)
                          )
                        }
                      />
                      {label}
                    </label>
                    <input
                      type="color"
                      aria-label={`Color of ${label}`}
                      value={colors[kind] ?? DEFAULT_SWATCH}
                      onChange={event =>
                        void setColors(c => ({
                          ...c,
                          [kind]: event.target.value,
                        }))
                      }
                    />
                  </div>
                );
              })}
            </fieldset>
          )}
          {(plugin === null || plugin.output_events.length > 0) && (
            <fieldset className="indicator-dialog__group">
              <legend>Markers</legend>
              <label className="j-check">
                <input
                  type="checkbox"
                  checked={eventsOn}
                  onChange={event => void setShowEvents(event.target.checked)}
                />
                Show event markers
              </label>
              {drawnEvents.map(event => {
                const label = eventLabel(plugin ?? undefined, event);
                return (
                  <div key={event} className="indicator-dialog__kind">
                    <span>
                      {label}{' '}
                      {label !== event && (
                        <span className="j-muted">
                          {event.toLowerCase().replace(/_/g, ' ')}
                        </span>
                      )}
                    </span>
                    <input
                      type="color"
                      aria-label={`Color of ${label}`}
                      value={colors[event] ?? DEFAULT_SWATCH}
                      onChange={e =>
                        void setColors(c => ({...c, [event]: e.target.value}))
                      }
                    />
                  </div>
                );
              })}
            </fieldset>
          )}
          {lines.length > 0 && (
            <fieldset className="indicator-dialog__group">
              <legend>Lines</legend>
              {lines.map(plot => (
                <div key={plot.value} className="indicator-dialog__kind">
                  <span>{plot.label}</span>
                  <input
                    type="color"
                    aria-label={`Color of ${plot.label}`}
                    value={colors[plot.value] ?? plot.color ?? DEFAULT_SWATCH}
                    onChange={event =>
                      void setColors(c => ({
                        ...c,
                        [plot.value]: event.target.value,
                      }))
                    }
                  />
                </div>
              ))}
            </fieldset>
          )}
          {panes.length > 0 && (
            <fieldset className="indicator-dialog__group">
              <legend>Panes</legend>
              {panes.map(pane => (
                <label key={pane.id} className="j-check">
                  <input
                    type="checkbox"
                    checked={paneOn(pane.id)}
                    data-testid={`indicator-pane-toggle-${pane.id}`}
                    onChange={event =>
                      void setPaneOverrides(p => ({
                        ...p,
                        [pane.id]: event.target.checked,
                      }))
                    }
                  />
                  Show the {pane.title} pane below the chart
                </label>
              ))}
            </fieldset>
          )}
          <footer className="dialog__foot">
            <button
              type="button"
              className="j-btn j-btn--ghost"
              onClick={reset}
            >
              Reset to defaults
            </button>
            <span className="dialog__spacer" />
            <button type="button" className="j-btn" onClick={close}>
              Cancel
            </button>
            <button
              type="submit"
              className="j-btn j-btn--primary"
              data-testid="indicator-settings-save"
            >
              Save
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
