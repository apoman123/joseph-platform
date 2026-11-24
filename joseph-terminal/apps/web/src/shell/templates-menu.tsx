/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Chart templates menu: save the active chart's presentation
 * under a name and apply saved templates to any chart.
 */

'use client';
import type {ChartState} from '@joseph/contracts';
import {createTemplate, type ChartTemplate} from '@joseph/terminal-shell';
import {Trash2} from 'lucide-react';
import {useState, type ReactElement} from 'react';
import {ids} from './ids';
import {useRuntime} from './runtime-context';

/** Templates menu props. */
export interface TemplatesMenuProps {
  /** Active chart. */
  chart: ChartState;
  /** Closes the menu. */
  close: () => void;
}

/**
 * Renders the templates menu body: a save form and the saved templates.
 * Saving reports invalid names inline; applying closes the menu.
 * @param props Props.
 * @return Menu content.
 */
export function TemplatesMenu({
  chart,
  close,
}: TemplatesMenuProps): ReactElement {
  const runtime = useRuntime();
  const [templates, setTemplates] = useState<ChartTemplate[]>(() =>
    runtime.templates.list()
  );
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const save = () => {
    try {
      const template = createTemplate(chart, name, ids.next(), Date.now());
      setTemplates(runtime.templates.save(template));
      setName('');
      setError(null);
      runtime.notifier.notify(`Saved template “${template.name}”`, 'success');
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Not saved');
    }
  };
  return (
    <div className="templates-menu">
      <p className="layers-menu__note">
        A template keeps the chart style, scale, volume, indicator layers with
        their inputs and colors, and session breaks. It never changes the
        instrument.
      </p>
      <form
        className="templates-menu__form"
        onSubmit={event => {
          event.preventDefault();
          save();
        }}
      >
        <label className="sr-only" htmlFor="template-name">
          Template name
        </label>
        <input
          id="template-name"
          className="j-input"
          placeholder="Name, e.g. Scalping"
          value={name}
          maxLength={60}
          onChange={event => void setName(event.target.value)}
        />
        <button
          type="submit"
          className="j-btn j-btn--sm"
          data-testid="template-save"
        >
          Save current
        </button>
      </form>
      {error && <p className="j-notice j-notice--danger">{error}</p>}
      {templates.length === 0 ? (
        <p className="j-muted templates-menu__empty">No templates yet.</p>
      ) : (
        <ul className="templates-menu__list" aria-label="Saved templates">
          {templates.map(template => (
            <li key={template.template_id}>
              <button
                type="button"
                className="j-menu__item"
                data-testid={`template-apply-${template.name}`}
                onClick={() => {
                  runtime.dispatch({
                    type: 'applyChartPresentation',
                    chartId: chart.chart_id,
                    presentation: template.presentation,
                  });
                  runtime.notifier.notify(
                    `Applied “${template.name}” to the active chart`,
                    'success'
                  );
                  close();
                }}
              >
                {template.name}
              </button>
              <button
                type="button"
                className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
                aria-label={`Delete template ${template.name}`}
                onClick={() =>
                  void setTemplates(
                    runtime.templates.remove(template.template_id)
                  )
                }
              >
                <Trash2 size={13} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
