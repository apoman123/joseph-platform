/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Body of the Layers menu: one row per study on the chart,
 * in legend order, with a visibility toggle and a settings button, then the
 * chart's volume, session break and data window options.
 */

'use client';
import type {ChartState} from '@joseph/contracts';
import type {StudyState} from '@joseph/terminal-shell';
import {Settings2} from 'lucide-react';
import type {ReactElement} from 'react';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {studyName, useStudyCatalog} from './study-catalog';

/**
 * Renders one study's visibility toggle and settings button.
 * @param props Chart and study.
 * @return Row.
 */
export function StudyRow({
  chart,
  study,
}: {
  chart: ChartState;
  study: StudyState;
}): ReactElement {
  const runtime = useRuntime();
  const catalog = useStudyCatalog();
  const chartId = chart.chart_id;
  const name = studyName(catalog, study);
  return (
    <div className="layers-menu__row">
      <label className="j-check layers-menu__item">
        <input
          type="checkbox"
          checked={study.visible}
          data-testid={`layer-toggle-${study.study_id}`}
          onChange={() =>
            void runtime.dispatch({
              type: 'updateStudy',
              chartId,
              studyId: study.study_id,
              patch: {visible: !study.visible},
            })
          }
        />
        {name}
      </label>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon j-btn--sm"
        aria-label={`${name} settings`}
        data-testid={`study-settings-${study.study_id}`}
        onClick={() =>
          void runtime.indicatorSettings.set({
            chartId,
            studyId: study.study_id,
          })
        }
      >
        <Settings2 size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

/**
 * Renders the chart's studies and display options.
 * @param props Chart, and whether to list the studies.
 * @return Menu body.
 */
export function LayersMenu({
  chart,
  showStudies = true,
}: {
  chart: ChartState;
  showStudies?: boolean;
}): ReactElement {
  const runtime = useRuntime();
  const dataWindow = useStore(runtime.dataWindow);
  const chartId = chart.chart_id;
  const run = (id: string) =>
    void runtime.commands.execute(id).catch(() => undefined);
  return (
    <div className="layers-menu">
      {showStudies && (
        <p className="layers-menu__note">
          Studies are computed by the Joseph engine and drawn as reported.
        </p>
      )}
      {showStudies &&
        chart.studies.map(study => (
          <StudyRow key={study.study_id} chart={chart} study={study} />
        ))}
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
    </div>
  );
}
