/**
 * @fileoverview Indicator settings dialog for one chart layer: inputs built
 * from the plugin's published `input_schema` (validated, then sent to the
 * engine with every query), per-kind visibility and colors, event markers
 * and the sub-pane toggle. Settings are saved in the workspace, so they
 * travel with templates, exports and share links.
 */
'use client';
import type {
  AoiKind,
  IndicatorPluginInfo,
  StructureEventName,
} from '@joseph/contracts';
import {PLUGIN_NAMES, STUDY_PLUGINS, markerText} from '@joseph/indicator-viz';
import {
  layerSettings,
  layerStyle,
  type IndicatorLayerStyle,
} from '@joseph/terminal-shell';
import {X} from 'lucide-react';
import {useEffect, useMemo, useRef, useState, type ReactElement} from 'react';
import type {TerminalRuntime} from '../composition/runtime';
import {
  collectOverrides,
  fieldLabel,
  fieldsFromSchema,
  initialValues,
} from './indicator-settings';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';

/** Plugin descriptors cached per runtime. */
const pluginCache = new WeakMap<
  TerminalRuntime,
  Promise<IndicatorPluginInfo[]>
>();

/**
 * Loads plugin descriptors once per runtime.
 * @param runtime Runtime.
 * @return Descriptors.
 */
function loadPlugins(runtime: TerminalRuntime): Promise<IndicatorPluginInfo[]> {
  let promise = pluginCache.get(runtime);
  if (!promise) {
    promise = runtime.clients.market
      .listIndicatorPlugins()
      .then(list => list.plugins);
    promise.catch(() => pluginCache.delete(runtime));
    pluginCache.set(runtime, promise);
  }
  return promise;
}

/** Fallback color per kind when the user has not picked one. */
const DEFAULT_SWATCH = '#8fa5b3';

/**
 * Indicator settings dialog (renders when a request is open).
 * @return Dialog or null.
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
 * The form for one chart layer.
 * @param props Chart and plugin ids.
 * @return Dialog.
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
    loadPlugins(runtime).then(
      plugins => {
        if (!alive) return;
        const found = plugins.find(p => p.plugin_id === pluginId) ?? null;
        setPlugin(found);
        if (!found) setLoadError('The engine does not publish this indicator.');
      },
      (failure: unknown) => {
        if (alive)
          setLoadError(
            failure instanceof Error ? failure.message : 'Request failed'
          );
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
  const [showEvents, setShowEvents] = useState(style.show_events !== false);
  const [pane, setPane] = useState(style.pane !== false);
  useEffect(() => {
    setValues(initialValues(fields, saved));
    // Only when the schema arrives; later edits are local until saved.
  }, [fields]); // eslint-disable-line react-hooks/exhaustive-deps
  const close = () => runtime.indicatorSettings.set(null);
  useEffect(() => {
    dialog.current
      ?.querySelector<HTMLElement>('input, select, button')
      ?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!chart) return <></>;
  const outputKinds = plugin?.output_kinds ?? [];
  const markedEvents = (plugin?.output_events ?? []).filter(
    (name: StructureEventName) => markerText(name) !== null
  );
  const shownKinds = kinds ?? outputKinds;
  const save = () => {
    const result = collectOverrides(fields, values);
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    const allKinds =
      kinds === null || outputKinds.every(k => kinds.includes(k));
    const nextStyle: IndicatorLayerStyle = {
      ...(Object.keys(colors).length > 0 ? {colors} : {}),
      ...(showEvents ? {} : {show_events: false}),
      ...(pane ? {} : {pane: false}),
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
      },
    });
    runtime.notifier.notify(
      `${PLUGIN_NAMES[pluginId] ?? pluginId} settings saved for this chart`,
      'success'
    );
    close();
  };
  const reset = () => {
    setValues(initialValues(fields, null));
    setErrors({});
    setKinds(null);
    setColors({});
    setShowEvents(true);
    setPane(true);
  };
  const name = plugin?.display_name ?? PLUGIN_NAMES[pluginId] ?? pluginId;
  return (
    <div className="dialog-backdrop" onPointerDown={close}>
      <div
        ref={dialog}
        className="dialog indicator-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="indicator-dialog-title"
        data-testid="indicator-settings"
        onPointerDown={event => event.stopPropagation()}
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
              Demo data is precomputed, so inputs are saved and sent but do not
              change the demo zones.
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
                            setValues(v => ({
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
                              setValues(v => ({
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
                              setValues(v => ({
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
              {outputKinds.map(kind => (
                <div key={kind} className="indicator-dialog__kind">
                  <label className="j-check">
                    <input
                      type="checkbox"
                      checked={shownKinds.includes(kind)}
                      onChange={event =>
                        setKinds(
                          event.target.checked
                            ? [...shownKinds, kind]
                            : shownKinds.filter(k => k !== kind)
                        )
                      }
                    />
                    {kind.replace(/_/g, ' ')}
                  </label>
                  <input
                    type="color"
                    aria-label={`Color of ${kind}`}
                    value={colors[kind] ?? DEFAULT_SWATCH}
                    onChange={event =>
                      setColors(c => ({...c, [kind]: event.target.value}))
                    }
                  />
                </div>
              ))}
            </fieldset>
          )}
          <fieldset className="indicator-dialog__group">
            <legend>Markers</legend>
            <label className="j-check">
              <input
                type="checkbox"
                checked={showEvents}
                onChange={event => setShowEvents(event.target.checked)}
              />
              Show event markers
            </label>
            {markedEvents.map(event => (
              <div key={event} className="indicator-dialog__kind">
                <span>
                  {markerText(event)}{' '}
                  <span className="j-muted">
                    {event.toLowerCase().replace(/_/g, ' ')}
                  </span>
                </span>
                <input
                  type="color"
                  aria-label={`Color of ${event}`}
                  value={colors[event] ?? DEFAULT_SWATCH}
                  onChange={e =>
                    setColors(c => ({...c, [event]: e.target.value}))
                  }
                />
              </div>
            ))}
            {STUDY_PLUGINS[pluginId] && (
              <label className="j-check">
                <input
                  type="checkbox"
                  checked={pane}
                  data-testid="indicator-pane-toggle"
                  onChange={event => setPane(event.target.checked)}
                />
                Show the Delta % pane below the chart
              </label>
            )}
          </fieldset>
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
