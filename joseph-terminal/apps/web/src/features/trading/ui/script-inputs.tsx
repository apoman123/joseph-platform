/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Form fields for the declared inputs of a catalog script
 * (`joseph_script.Input`): a choice list for options, a checkbox for
 * booleans, a number field with the declared bounds, else a text field.
 */

'use client';
import type {CatalogInput, JsonValue} from '@joseph/contracts';
import {useEffect, useState, type ReactNode} from 'react';

/**
 * Merges overrides over declared defaults.
 * @param inputs Declared inputs.
 * @param values Overrides.
 * @return Effective values.
 */
export function effectiveInputs(
  inputs: Readonly<Record<string, CatalogInput>>,
  values: Readonly<Record<string, JsonValue | undefined>>
): Record<string, JsonValue> {
  const merged: Record<string, JsonValue> = {};
  for (const [name, spec] of Object.entries(inputs)) {
    merged[name] = values[name] ?? spec.default;
  }
  return merged;
}

/** Props of `NumberField`. */
interface NumberFieldProps {
  /** Element id. */
  id: string;
  /** Current number. */
  value: number;
  /** Declaration with bounds and step. */
  spec: CatalogInput;
  /**
   * Reports a new finite number.
   * @param value Number typed.
   */
  onChange(value: number): void;
}

/**
 * Renders a number field that keeps partial text (such as an empty field
 * while retyping) and reports only finite numbers.
 * @param props Value, bounds and change handler.
 * @return Input element.
 */
function NumberField({id, value, spec, onChange}: NumberFieldProps): ReactNode {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => {
    setDraft(current => (Number(current) === value ? current : String(value)));
  }, [value]);
  return (
    <input
      id={id}
      className="j-input j-num"
      type="number"
      value={draft}
      min={spec.minimum ?? undefined}
      max={spec.maximum ?? undefined}
      step={spec.step ?? 'any'}
      onChange={e => {
        setDraft(e.target.value);
        const parsed = Number(e.target.value);
        if (e.target.value !== '' && Number.isFinite(parsed)) onChange(parsed);
      }}
    />
  );
}

/** Props of `ScriptInputs`. */
export interface ScriptInputsProps {
  /** Declared inputs in display order. */
  inputs: Readonly<Record<string, CatalogInput>>;
  /** Current values (overrides of the defaults). */
  values: Readonly<Record<string, JsonValue | undefined>>;
  /**
   * Reports a changed value.
   * @param name Input name.
   * @param value New value.
   */
  onChange(name: string, value: JsonValue): void;
  /** Prefix for element ids, unique on the page. */
  idPrefix: string;
}

/**
 * Renders one field per declared input.
 * @param props Inputs, values and change handler.
 * @return Fields.
 */
export function ScriptInputs({
  inputs,
  values,
  onChange,
  idPrefix,
}: ScriptInputsProps): ReactNode {
  const merged = effectiveInputs(inputs, values);
  return Object.entries(inputs).map(([name, spec]) => {
    const id = `${idPrefix}-${name}`;
    const value = merged[name];
    const label = spec.title || name;
    if (spec.options) {
      return (
        <label key={name} className="j-field" htmlFor={id} title={spec.tooltip}>
          <span className="j-label">{label}</span>
          <select
            id={id}
            className="j-select"
            value={String(value)}
            onChange={e => {
              const option = spec.options?.find(
                o => String(o) === e.target.value
              );
              onChange(name, option ?? e.target.value);
            }}
          >
            {spec.options.map(option => (
              <option key={String(option)} value={String(option)}>
                {String(option).replace('_', ' ')}
              </option>
            ))}
          </select>
        </label>
      );
    }
    if (typeof spec.default === 'boolean') {
      return (
        <label key={name} className="j-check" title={spec.tooltip}>
          <input
            id={id}
            type="checkbox"
            checked={value === true}
            onChange={e => void onChange(name, e.target.checked)}
          />
          {label}
        </label>
      );
    }
    if (typeof spec.default === 'number') {
      return (
        <label key={name} className="j-field" htmlFor={id} title={spec.tooltip}>
          <span className="j-label">{label}</span>
          <NumberField
            id={id}
            value={Number(value)}
            spec={spec}
            onChange={next => void onChange(name, next)}
          />
        </label>
      );
    }
    return (
      <label key={name} className="j-field" htmlFor={id} title={spec.tooltip}>
        <span className="j-label">{label}</span>
        <input
          id={id}
          className="j-input"
          value={String(value ?? '')}
          onChange={e => void onChange(name, e.target.value)}
        />
      </label>
    );
  });
}
