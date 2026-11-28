/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Settings dialog of one chart study, generated from the
 * script's declared inputs: an Inputs section per input group, then the
 * Style inputs and per-plot color overrides. Owned scripts without
 * declared inputs edit the inputs their version stores. Saving changes this
 * chart's study only.
 */

'use client';
import type {CatalogInput} from '@joseph/contracts';
import type {StudyState} from '@joseph/terminal-shell';
import {X} from 'lucide-react';
import {useEffect, useRef, useState, type ReactElement} from 'react';
import {pythonState} from './python-state';
import {studyKey} from './python-studies';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {catalogEntry, studyName, useStudyCatalog} from './study-catalog';

/** Hex colors the color inputs accept. */
const HEX = /^#[0-9a-fA-F]{6}$/;

/** One editable input. */
export interface StudyField {
  /** Input name. */
  name: string;
  /** Declared input, or a generic one for owned scripts. */
  spec: CatalogInput;
}

/**
 * Builds the form fields of a study.
 * @param inputs Declared inputs (catalog), or null for owned scripts.
 * @param saved Saved input values (owned scripts list theirs here).
 * @return Fields in declaration order.
 */
export function studyFields(
  inputs: Readonly<Record<string, CatalogInput | undefined>> | null,
  saved: Readonly<Record<string, unknown>>
): StudyField[] {
  if (inputs) {
    return Object.entries(inputs).flatMap(([name, spec]) =>
      spec ? [{name, spec}] : []
    );
  }
  return Object.entries(saved).map(([name, value]) => ({
    name,
    spec: {
      default: value,
      title: '',
      minimum: null,
      maximum: null,
      options: null,
      step: null,
      group: '',
      tooltip: '',
    },
  }));
}

/**
 * Validates edited values against their declarations.
 * @param fields Form fields.
 * @param values Edited values.
 * @return Inputs that differ from the defaults, or per-field errors.
 */
export function collectInputs(
  fields: readonly StudyField[],
  values: Readonly<Record<string, unknown>>
):
  | {ok: true; inputs: Record<string, unknown>}
  | {ok: false; errors: Record<string, string>} {
  const inputs: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const {name, spec} of fields) {
    let value = values[name];
    if (typeof spec.default === 'number') {
      const number = Number(value);
      if (String(value ?? '').trim() === '' || !Number.isFinite(number)) {
        errors[name] = 'Enter a number';
        continue;
      }
      if (spec.minimum !== null && number < spec.minimum) {
        errors[name] = `At least ${spec.minimum}`;
        continue;
      }
      if (spec.maximum !== null && number > spec.maximum) {
        errors[name] = `At most ${spec.maximum}`;
        continue;
      }
      value =
        Number.isInteger(spec.default) && spec.step === 1
          ? Math.round(number)
          : number;
    }
    if (
      typeof spec.default === 'string' &&
      spec.default.startsWith('#') &&
      !HEX.test(String(value))
    ) {
      errors[name] = 'Use a #rrggbb color';
      continue;
    }
    if (value !== spec.default) inputs[name] = value;
  }
  return Object.keys(errors).length ? {ok: false, errors} : {ok: true, inputs};
}

/**
 * Renders the settings dialog requested through `runtime.indicatorSettings`.
 * @return Dialog, or null while none is requested.
 */
export function IndicatorSettingsDialog(): ReactElement | null {
  const runtime = useRuntime();
  const request = useStore(runtime.indicatorSettings);
  const workspace = useStore(runtime.workspace);
  if (!request) return null;
  const study = workspace.charts
    .find(chart => chart.chart_id === request.chartId)
    ?.studies.find(item => item.study_id === request.studyId);
  if (!study) return null;
  return (
    <SettingsForm
      key={`${request.chartId}:${request.studyId}`}
      chartId={request.chartId}
      study={study}
    />
  );
}

/**
 * Renders the form of one study.
 * @param props Chart id and study.
 * @return Modal dialog.
 */
function SettingsForm({
  chartId,
  study,
}: {
  chartId: string;
  study: StudyState;
}): ReactElement {
  const runtime = useRuntime();
  const catalog = useStudyCatalog();
  const results = useStore(pythonState(runtime).results);
  const entry = catalogEntry(catalog, study);
  const saved = study.inputs as Record<string, unknown>;
  const fields = studyFields(entry?.inputs ?? null, saved);
  const initial = () =>
    Object.fromEntries(
      fields.map(({name, spec}) => [name, saved[name] ?? spec.default])
    );
  const [values, setValues] = useState<Record<string, unknown>>(initial);
  const [colors, setColors] = useState<Record<string, string>>({
    ...(study.colors as Record<string, string>),
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const dialog = useRef<HTMLDivElement>(null);
  const result = results[studyKey(chartId, study.study_id)];
  const plots = (result?.plots ?? []).filter(
    plot => result?.plot_styles?.[plot.name]?.display !== 'data_window'
  );
  const name = studyName(catalog, study);
  const close = () => void runtime.indicatorSettings.set(null);
  useEffect(() => {
    // Declared inputs arrive with the catalog after the first render.
    setValues(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reinitializes only when the declarations arrive; edits stay local until saved
  }, [entry]);
  useEffect(() => {
    dialog.current
      ?.querySelector<HTMLElement>('input, select, button')
      ?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKey);
    return () => void window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- set up once per dialog; `close` only clears a store
  }, []);
  const save = () => {
    const collected = collectInputs(fields, values);
    if (!collected.ok) {
      setErrors(collected.errors);
      return;
    }
    runtime.dispatch({
      type: 'updateStudy',
      chartId,
      studyId: study.study_id,
      patch: {inputs: collected.inputs, colors},
    });
    runtime.notifier.notify(`${name} settings saved for this chart`, 'success');
    close();
  };
  const reset = () => {
    setValues(
      Object.fromEntries(fields.map(({name: key, spec}) => [key, spec.default]))
    );
    setColors({});
    setErrors({});
  };
  const groups = new Map<string, StudyField[]>();
  for (const field of fields) {
    const group = field.spec.group || 'Inputs';
    groups.set(group, [...(groups.get(group) ?? []), field]);
  }
  const style = groups.get('Style') ?? [];
  groups.delete('Style');
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
          <h2 id="indicator-dialog-title">{entry?.title ?? name}</h2>
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
          {entry?.description && (
            <p className="indicator-dialog__help">{entry.description}</p>
          )}
          {[...groups].map(([group, items]) => (
            <fieldset key={group} className="indicator-dialog__group">
              <legend>{group}</legend>
              {items.map(field => (
                <InputField
                  key={field.name}
                  field={field}
                  value={values[field.name]}
                  error={errors[field.name]}
                  onChange={value =>
                    void setValues(v => ({...v, [field.name]: value}))
                  }
                />
              ))}
            </fieldset>
          ))}
          {(style.length > 0 || plots.length > 0) && (
            <fieldset className="indicator-dialog__group">
              <legend>Style</legend>
              {style.map(field => (
                <InputField
                  key={field.name}
                  field={field}
                  value={values[field.name]}
                  error={errors[field.name]}
                  onChange={value =>
                    void setValues(v => ({...v, [field.name]: value}))
                  }
                />
              ))}
              {plots.map(plot => (
                <div key={plot.name} className="indicator-dialog__kind">
                  <span>{plot.name}</span>
                  <input
                    type="color"
                    aria-label={`Color of ${plot.name}`}
                    value={
                      colors[plot.name] ??
                      String(
                        result?.plot_styles?.[plot.name]?.color ?? '#5cb8e6'
                      ).slice(0, 7)
                    }
                    onChange={event =>
                      void setColors(c => ({
                        ...c,
                        [plot.name]: event.target.value,
                      }))
                    }
                  />
                </div>
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

/**
 * Renders one input control: checkbox, choice list, color, number or text.
 * @param props Field, current value, error and change callback.
 * @return Field markup with its help text.
 */
function InputField({
  field,
  value,
  error,
  onChange,
}: {
  field: StudyField;
  value: unknown;
  error: string | undefined;
  onChange: (value: unknown) => void;
}): ReactElement {
  const {name, spec} = field;
  const id = `input-${name}`;
  const label = spec.title || name.replace(/_/g, ' ');
  const help =
    error ??
    `${spec.tooltip ? `${spec.tooltip} ` : ''}Default ${String(spec.default)}.`;
  if (typeof spec.default === 'boolean') {
    return (
      <div className="indicator-dialog__field">
        <label className="j-check">
          <input
            id={id}
            type="checkbox"
            checked={value === true}
            onChange={event => void onChange(event.target.checked)}
          />
          {label}
        </label>
        <p className="indicator-dialog__help">{help}</p>
      </div>
    );
  }
  let control: ReactElement;
  if (spec.options) {
    control = (
      <select
        id={id}
        className="j-select"
        value={String(value ?? '')}
        onChange={event => {
          const option = spec.options?.find(
            item => String(item) === event.target.value
          );
          onChange(option ?? event.target.value);
        }}
      >
        {spec.options.map(option => (
          <option key={String(option)} value={String(option)}>
            {String(option)}
          </option>
        ))}
      </select>
    );
  } else if (typeof spec.default === 'string' && spec.default.startsWith('#')) {
    control = (
      <input
        id={id}
        type="color"
        value={HEX.test(String(value)) ? String(value) : spec.default}
        onChange={event => void onChange(event.target.value)}
      />
    );
  } else {
    control = (
      <input
        id={id}
        className="j-input j-tabular"
        inputMode={typeof spec.default === 'number' ? 'decimal' : 'text'}
        value={String(value ?? '')}
        aria-invalid={error ? true : undefined}
        aria-describedby={`${id}-help`}
        onChange={event => void onChange(event.target.value)}
      />
    );
  }
  return (
    <div className="indicator-dialog__field">
      <label className="j-label" htmlFor={id}>
        {label}
      </label>
      {control}
      <p
        id={`${id}-help`}
        className={error ? 'indicator-dialog__error' : 'indicator-dialog__help'}
      >
        {help}
      </p>
    </div>
  );
}
