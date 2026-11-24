/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Pure helpers for the indicator settings form: reads a
 * plugin's published `input_schema`, validates user input against it and
 * reduces the result to overrides (values that differ from the defaults).
 */

import {Decimal} from '@joseph/contracts';

/** Supported field kinds; decimals are strings to keep exact values. */
export type InputKind = 'integer' | 'decimal' | 'boolean' | 'enum';

/** One form field derived from the schema. */
export interface InputField {
  /** Setting key, e.g. `min_delta_share`. */
  name: string;
  /** Field kind. */
  kind: InputKind;
  /** Human description from the engine. */
  description: string;
  /** Default value. */
  defaultValue: string | number | boolean;
  /** Inclusive minimum (numbers as strings for decimals). */
  minimum?: number | string;
  /** Inclusive maximum (numbers as strings for decimals). */
  maximum?: number | string;
  /** Allowed values of enum fields. */
  choices?: readonly string[];
}

/** Settings filled by the platform rather than the user. */
const PLATFORM_FIELDS: ReadonlySet<string> = new Set(['tick_size']);

/**
 * Reads form fields from a JSON Schema object (engine `SettingSpec` shape).
 * Unknown property shapes are skipped rather than guessed.
 * @param schema `input_schema` of a plugin.
 * @return Fields in schema order.
 */
export function fieldsFromSchema(schema: unknown): InputField[] {
  // Optional chaining tolerates any value; only an object whose
  // `properties` is an object yields fields.
  const properties = (schema as {properties?: unknown} | null)?.properties;
  if (!properties || typeof properties !== 'object') return [];
  const fields: InputField[] = [];
  for (const [name, raw] of Object.entries(
    properties as Record<string, unknown>
  )) {
    if (PLATFORM_FIELDS.has(name) || !raw || typeof raw !== 'object') continue;
    const prop = raw as Record<string, unknown>;
    const description =
      typeof prop.description === 'string' ? prop.description : name;
    // The engine publishes bounds as numbers, or as decimal strings for
    // decimal settings.
    const bounds = {
      ...(prop.minimum !== undefined
        ? {minimum: prop.minimum as number | string}
        : {}),
      ...(prop.maximum !== undefined
        ? {maximum: prop.maximum as number | string}
        : {}),
    };
    if (Array.isArray(prop.enum)) {
      const choices = prop.enum.filter(
        (v): v is string => typeof v === 'string'
      );
      fields.push({
        name,
        kind: 'enum',
        description,
        defaultValue:
          typeof prop.default === 'string' ? prop.default : (choices[0] ?? ''),
        choices,
      });
    } else if (prop.type === 'integer' && typeof prop.default === 'number') {
      fields.push({
        name,
        kind: 'integer',
        description,
        defaultValue: prop.default,
        ...bounds,
      });
    } else if (prop.type === 'boolean' && typeof prop.default === 'boolean') {
      fields.push({
        name,
        kind: 'boolean',
        description,
        defaultValue: prop.default,
      });
    } else if (prop.type === 'string' && typeof prop.default === 'string') {
      fields.push({
        name,
        kind: 'decimal',
        description,
        defaultValue: prop.default,
        ...bounds,
      });
    }
  }
  return fields;
}

/**
 * Turns a setting key into a sentence-case label.
 * @param name Key such as `min_delta_share`.
 * @return Label such as `Min delta share`.
 */
export function fieldLabel(name: string): string {
  const words = name.replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Compares two decimal strings exactly.
 * @param a First decimal.
 * @param b Second decimal.
 * @return -1, 0 or 1 as `a` is less than, equal to or greater than `b`.
 */
function cmp(a: string, b: string): number {
  return Decimal.parse(a).cmp(Decimal.parse(b));
}

/**
 * Validates one raw form value against its field's kind and bounds.
 * @param field Field definition.
 * @param raw Value from the form (strings for text inputs).
 * @return Parsed value (decimals stay strings) or an error message.
 */
export function parseFieldValue(
  field: InputField,
  raw: string | boolean
): {ok: true; value: string | number | boolean} | {ok: false; error: string} {
  if (field.kind === 'boolean') return {ok: true, value: raw === true};
  const text = String(raw).trim();
  if (field.kind === 'enum') {
    return field.choices?.includes(text)
      ? {ok: true, value: text}
      : {ok: false, error: `Choose one of ${field.choices?.join(', ')}`};
  }
  if (field.kind === 'integer') {
    if (!/^-?\d+$/.test(text)) {
      return {ok: false, error: 'Enter a whole number'};
    }
    const value = Number(text);
    if (field.minimum !== undefined && value < Number(field.minimum)) {
      return {ok: false, error: `At least ${field.minimum}`};
    }
    if (field.maximum !== undefined && value > Number(field.maximum)) {
      return {ok: false, error: `At most ${field.maximum}`};
    }
    return {ok: true, value};
  }
  // Plain notation only: no exponent, leading zeros or `+` sign, even where
  // `Decimal` would accept them.
  if (!Decimal.isValid(text) || !/^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(text)) {
    return {ok: false, error: 'Enter a decimal number such as 0.25'};
  }
  if (field.minimum !== undefined && cmp(text, String(field.minimum)) < 0) {
    return {ok: false, error: `At least ${field.minimum}`};
  }
  if (field.maximum !== undefined && cmp(text, String(field.maximum)) > 0) {
    return {ok: false, error: `At most ${field.maximum}`};
  }
  return {ok: true, value: text};
}

/**
 * Validates every field and keeps only values that differ from defaults
 * (decimals compared by value, so `0.50` equals `0.5`).
 * @param fields Field definitions.
 * @param raw Raw form values by setting key; missing keys are skipped.
 * @return Overrides (empty = plugin defaults) or errors by setting key.
 */
export function collectOverrides(
  fields: readonly InputField[],
  raw: Readonly<Record<string, string | boolean>>
):
  | {ok: true; overrides: Record<string, string | number | boolean>}
  | {ok: false; errors: Record<string, string>} {
  const overrides: Record<string, string | number | boolean> = {};
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const value = raw[field.name];
    if (value === undefined) continue;
    const parsed = parseFieldValue(field, value);
    if (!parsed.ok) {
      errors[field.name] = parsed.error;
      continue;
    }
    const same =
      field.kind === 'decimal'
        ? cmp(String(parsed.value), String(field.defaultValue)) === 0
        : parsed.value === field.defaultValue;
    if (!same) overrides[field.name] = parsed.value;
  }
  return Object.keys(errors).length > 0
    ? {ok: false, errors}
    : {ok: true, overrides};
}

/**
 * Returns the initial form values: saved overrides over defaults.
 * @param fields Field definitions.
 * @param saved Saved overrides, or null when none are saved.
 * @return Raw form values by setting key.
 */
export function initialValues(
  fields: readonly InputField[],
  saved: Readonly<Record<string, unknown>> | null
): Record<string, string | boolean> {
  const values: Record<string, string | boolean> = {};
  for (const field of fields) {
    const value = saved?.[field.name] ?? field.defaultValue;
    values[field.name] =
      field.kind === 'boolean' ? value === true : String(value);
  }
  return values;
}
