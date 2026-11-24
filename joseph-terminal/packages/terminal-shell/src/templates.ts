/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Chart templates: named chart presentations (style, scale,
 * volume, indicator layers with their inputs and colors, separators) that
 * can be applied to any chart. Templates never carry an instrument, so
 * applying one cannot move a chart to another venue.
 */

import type {ChartState} from '@joseph/contracts';
import type {WorkspaceStorage} from './persistence';
import type {ChartPresentation} from './workspace';

/** Storage key of the template list, stored as one JSON array (schema v1). */
export const TEMPLATE_STORAGE_KEY = 'joseph.chartTemplates.v1';

/** Maximum templates kept; saving beyond it drops the oldest. */
export const MAX_TEMPLATES = 50;

/** Saved chart template. */
export interface ChartTemplate {
  /** Document version. */
  schema_version: 1;
  /** Unique id. */
  template_id: string;
  /** User-visible name, unique ignoring case. */
  name: string;
  /** Creation time (UTC epoch ms). */
  created_at_ms: number;
  /** Captured presentation. */
  presentation: ChartPresentation;
}

/**
 * Captures a chart's presentation as a deep copy.
 * @param chart Chart to capture.
 * @return Presentation only: no chart id, instrument, timeframe,
 *     comparisons, feed mode, link group or viewport.
 */
export function presentationOf(chart: ChartState): ChartPresentation {
  return {
    chart_type: chart.chart_type,
    price_scale: chart.price_scale,
    show_volume: chart.show_volume,
    indicator_layers: structuredClone(chart.indicator_layers),
    session_separators: chart.session_separators === true,
  };
}

/**
 * Creates a template from a chart.
 * @param chart Chart to capture.
 * @param name Template name; trimmed and cut to 60 characters.
 * @param id Unique template id.
 * @param nowMs Creation time (UTC epoch ms).
 * @return Template.
 * @throws `Error` when the name is empty after trimming; its message is
 *     shown to the user.
 */
export function createTemplate(
  chart: ChartState,
  name: string,
  id: string,
  nowMs: number
): ChartTemplate {
  const trimmed = name.trim().slice(0, 60);
  if (!trimmed) throw new Error('Give the template a name');
  return {
    schema_version: 1,
    template_id: id,
    name: trimmed,
    created_at_ms: nowMs,
    presentation: presentationOf(chart),
  };
}

/**
 * Validates an untrusted template's shape. The layer entries and enum
 * values are not checked.
 * @param value Candidate, usually from storage.
 * @return True when the value has the template shape.
 */
export function isChartTemplate(value: unknown): value is ChartTemplate {
  if (!value || typeof value !== 'object') return false;
  const t = value as Partial<ChartTemplate>;
  const p = t.presentation as Partial<ChartPresentation> | undefined;
  return (
    t.schema_version === 1 &&
    typeof t.template_id === 'string' &&
    typeof t.name === 'string' &&
    typeof t.created_at_ms === 'number' &&
    !!p &&
    typeof p.chart_type === 'string' &&
    typeof p.price_scale === 'string' &&
    typeof p.show_volume === 'boolean' &&
    Array.isArray(p.indicator_layers)
  );
}

/** Loads and saves templates; unreadable entries are skipped, not lost. */
export class TemplateRepository {
  /**
   * Creates the repository.
   * @param storage Storage holding the list under `TEMPLATE_STORAGE_KEY`.
   */
  constructor(private readonly storage: WorkspaceStorage) {}

  /**
   * Loads templates, newest first.
   * @return Valid templates; empty when nothing is stored or the stored text
   *     is not a JSON array.
   */
  list(): ChartTemplate[] {
    try {
      const raw = this.storage.getItem(TEMPLATE_STORAGE_KEY);
      const parsed: unknown = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed)
        ? parsed
            .filter(isChartTemplate)
            .sort((a, b) => b.created_at_ms - a.created_at_ms)
        : [];
    } catch {
      return [];
    }
  }

  /**
   * Saves a template first in the list, replacing any template whose name
   * matches ignoring case, and keeps at most `MAX_TEMPLATES`.
   * @param template Template to save.
   * @return Updated list, newest first.
   */
  save(template: ChartTemplate): ChartTemplate[] {
    const others = this.list().filter(
      t => t.name.toLowerCase() !== template.name.toLowerCase()
    );
    const next = [template, ...others].slice(0, MAX_TEMPLATES);
    this.storage.setItem(TEMPLATE_STORAGE_KEY, JSON.stringify(next));
    return next;
  }

  /**
   * Deletes a template; an unknown id leaves the list as it is.
   * @param templateId Id of the template to delete.
   * @return Updated list, newest first.
   */
  remove(templateId: string): ChartTemplate[] {
    const next = this.list().filter(t => t.template_id !== templateId);
    this.storage.setItem(TEMPLATE_STORAGE_KEY, JSON.stringify(next));
    return next;
  }
}
