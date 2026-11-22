/**
 * @fileoverview Chart templates: named chart presentations (style, scale,
 * volume, indicator layers with their inputs and colors, separators) that
 * can be applied to any chart. Templates never carry an instrument, so
 * applying one cannot move a chart to another venue.
 */
import type {ChartState} from '@joseph/contracts';
import type {WorkspaceStorage} from './persistence';
import type {ChartPresentation} from './workspace';

/** Storage key of the template list (schema version 1). */
export const TEMPLATE_STORAGE_KEY = 'joseph.chartTemplates.v1';

/** Maximum templates kept. */
export const MAX_TEMPLATES = 50;

/** Saved chart template. */
export interface ChartTemplate {
  /** Document version. */
  schema_version: 1;
  /** Unique id. */
  template_id: string;
  /** User-visible name. */
  name: string;
  /** Creation time (UTC ms). */
  created_at_ms: number;
  /** Captured presentation. */
  presentation: ChartPresentation;
}

/**
 * Captures a chart's presentation.
 * @param chart Chart state.
 * @return Presentation without instrument, range or link settings.
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
 * @param chart Chart state.
 * @param name Template name (trimmed, max 60 characters).
 * @param id Unique id.
 * @param nowMs Clock.
 * @return Template.
 * @throws {Error} When the name is empty.
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
 * Validates an untrusted template.
 * @param value Candidate.
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
   * @param storage Storage.
   */
  constructor(private readonly storage: WorkspaceStorage) {}

  /**
   * Loads templates, newest first.
   * @return Templates.
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
   * Saves a template, replacing one with the same name.
   * @param template Template.
   * @return Updated list.
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
   * Deletes a template.
   * @param templateId Template id.
   * @return Updated list.
   */
  remove(templateId: string): ChartTemplate[] {
    const next = this.list().filter(t => t.template_id !== templateId);
    this.storage.setItem(TEMPLATE_STORAGE_KEY, JSON.stringify(next));
    return next;
  }
}
