/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Heatmap toolbar: data set (grouped by family and country),
 * Size by, Color by, Group by, display settings, share/export, refresh and
 * fullscreen. Options a source cannot serve stay listed but disabled, with
 * the reason in their label and title.
 */

'use client';
import type {
  HeatmapCatalog,
  HeatmapDataset,
  HeatmapGrouping,
  HeatmapMetric,
} from '@joseph/contracts';
import {Camera, Maximize2, Minimize2, RefreshCw, Settings2} from 'lucide-react';
import {useId, type ReactElement} from 'react';
import {Popover} from '../popover';
import {SCHEMES} from './colors';
import type {HeatmapSettings, HeatmapType} from './model';
import styles from './heatmap.module.css';

/** Family labels in menu order. */
const FAMILIES: ReadonlyArray<[HeatmapType, string]> = [
  ['stocks', 'Stocks'],
  ['etfs', 'ETFs'],
  ['crypto', 'Crypto coins'],
];

/** Toolbar props. */
export interface ToolbarProps {
  /** Catalogue, or null until loaded. */
  catalog: HeatmapCatalog | null;
  /** Current settings. */
  settings: HeatmapSettings;
  /** Data set in effect. */
  dataset: string;
  /** Data set label (shown before the catalogue loads). */
  datasetLabel: string;
  /** Size options. */
  sizes: readonly HeatmapMetric[];
  /** Colour options. */
  colors: readonly HeatmapMetric[];
  /** Grouping options. */
  groupings: readonly HeatmapGrouping[];
  /** Size in effect. */
  size: string;
  /** Colour in effect. */
  color: string;
  /** Grouping in effect. */
  grouping: string;
  /** Applies a settings patch. */
  onChange: (patch: Partial<HeatmapSettings>) => void;
  /** Reloads now. */
  onRefresh: () => void;
  /** Whether a load is in flight. */
  loading: boolean;
  /** Downloads a PNG. */
  onDownload: () => void;
  /** Copies a PNG. */
  onCopyImage: () => void;
  /** Copies a link to this view. */
  onCopyLink: () => void;
  /** Whether the heatmap is fullscreen. */
  fullscreen: boolean;
  /** Toggles fullscreen. */
  onFullscreen: () => void;
}

/**
 * Labels an option, adding the reason when it is unavailable.
 * @param row Option.
 * @return Label text.
 */
function optionLabel(row: {
  label: string;
  available: boolean;
  reason?: string | null;
}): string {
  return row.available ? row.label : `${row.label} — unavailable`;
}

/**
 * Renders the data-set select grouped by family and section.
 * @param props Catalogue, current data set and change callback.
 * @return Labelled select.
 */
function DatasetSelect({
  catalog,
  dataset,
  datasetLabel,
  onPick,
}: {
  catalog: HeatmapCatalog | null;
  dataset: string;
  datasetLabel: string;
  onPick: (row: HeatmapDataset) => void;
}): ReactElement {
  const id = useId();
  const rows = catalog?.datasets ?? [];
  const sections = FAMILIES.flatMap(([type, family]) => {
    const own = rows.filter(row => row.type === type);
    return [...new Set(own.map(row => row.section))].map(section => ({
      label: type === 'crypto' ? family : `${family} · ${section}`,
      rows: own.filter(row => row.section === section),
    }));
  });
  return (
    <label className={styles.control} htmlFor={id}>
      <span>Data set</span>
      <select
        id={id}
        className="j-select"
        value={dataset}
        onChange={event => {
          const row = rows.find(item => item.id === event.target.value);
          if (row) onPick(row);
        }}
      >
        {!rows.some(row => row.id === dataset) && (
          <option value={dataset}>{datasetLabel}</option>
        )}
        {sections.map(section => (
          <optgroup key={section.label} label={section.label}>
            {section.rows.map(row => (
              <option
                key={row.id}
                value={row.id}
                disabled={!row.available}
                title={row.reason ?? undefined}
              >
                {optionLabel(row)}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}

/**
 * Renders a labelled select of metric or grouping options.
 * @param props Label, options, value and change callback.
 * @return Labelled select.
 */
function OptionSelect({
  label,
  options,
  value,
  onPick,
}: {
  label: string;
  options: ReadonlyArray<{
    id: string;
    label: string;
    available: boolean;
    reason?: string | null;
  }>;
  value: string;
  onPick: (id: string) => void;
}): ReactElement {
  const id = useId();
  const current = options.find(row => row.id === value);
  return (
    <label className={styles.control} htmlFor={id}>
      <span>{label}</span>
      <select
        id={id}
        className="j-select"
        value={value}
        title={current?.reason ?? undefined}
        onChange={event => void onPick(event.target.value)}
      >
        {options.map(row => (
          <option
            key={row.id}
            value={row.id}
            disabled={!row.available}
            title={row.reason ?? undefined}
          >
            {optionLabel(row)}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * Renders a radio group of the display settings.
 * @param props Legend, options, value and change callback.
 * @return Fieldset.
 */
function RadioGroup<T extends string>({
  legend,
  options,
  value,
  onPick,
}: {
  legend: string;
  options: ReadonlyArray<[T, string]>;
  value: T;
  onPick: (value: T) => void;
}): ReactElement {
  const name = useId();
  return (
    <fieldset className={styles.fieldset}>
      <legend>{legend}</legend>
      {options.map(([id, label]) => (
        <label key={id} className={styles.radio}>
          <input
            type="radio"
            name={name}
            value={id}
            checked={value === id}
            onChange={() => void onPick(id)}
          />
          {label}
        </label>
      ))}
    </fieldset>
  );
}

/**
 * Renders the toolbar.
 * @param props Toolbar props.
 * @return Toolbar element.
 */
export function HeatmapToolbar(props: ToolbarProps): ReactElement {
  const {settings, onChange} = props;
  const colorLabel =
    props.colors.find(row => row.id === props.color)?.label ?? 'Colour value';
  const sizeLabel =
    props.sizes.find(row => row.id === props.size)?.label ?? 'Size value';
  return (
    <div className={styles.toolbar} role="toolbar" aria-label="Heatmap options">
      <DatasetSelect
        catalog={props.catalog}
        dataset={props.dataset}
        datasetLabel={props.datasetLabel}
        onPick={row =>
          void onChange(
            row.type === settings.type
              ? {dataset: row.id, zoom: null}
              : {
                  type: row.type,
                  dataset: row.id,
                  size: null,
                  color: null,
                  grouping: null,
                  zoom: null,
                }
          )
        }
      />
      <OptionSelect
        label="Size by"
        options={props.sizes}
        value={props.size}
        onPick={size => void onChange({size})}
      />
      <OptionSelect
        label="Color by"
        options={props.colors}
        value={props.color}
        onPick={color => void onChange({color})}
      />
      {props.groupings.length > 1 && (
        <OptionSelect
          label="Group by"
          options={props.groupings}
          value={props.grouping}
          onPick={grouping =>
            void onChange({
              grouping: grouping as HeatmapSettings['grouping'],
              zoom: null,
            })
          }
        />
      )}
      <span className={styles.spacer} />
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon"
        aria-label="Refresh heatmap"
        title="Refresh now"
        disabled={props.loading}
        onClick={props.onRefresh}
      >
        <RefreshCw size={16} aria-hidden="true" />
      </button>
      <Popover
        label={<Settings2 size={16} aria-hidden="true" />}
        triggerLabel="Display settings"
        ariaLabel="Display settings"
        className="j-btn j-btn--ghost j-btn--icon"
        alignRight
      >
        {() => (
          <div className={styles.settings}>
            <RadioGroup
              legend="Title"
              value={settings.title}
              options={[
                ['none', 'None'],
                ['ticker', 'Symbol ticker'],
                ['description', 'Symbol description'],
              ]}
              onPick={title => void onChange({title})}
            />
            <label className={styles.radio}>
              <input
                type="checkbox"
                checked={settings.logo}
                onChange={event => void onChange({logo: event.target.checked})}
              />
              Logo (letter badge)
            </label>
            <RadioGroup
              legend="Display value"
              value={settings.value}
              options={[
                ['none', 'None'],
                ['color', colorLabel],
                ['price', 'Symbol price'],
                ['size', sizeLabel],
              ]}
              onPick={value => void onChange({value})}
            />
            <RadioGroup
              legend="Color scheme"
              value={settings.scheme}
              options={SCHEMES.map(row => [row.id, row.label] as const)}
              onPick={scheme => void onChange({scheme})}
            />
          </div>
        )}
      </Popover>
      <Popover
        label={<Camera size={16} aria-hidden="true" />}
        triggerLabel="Share and export"
        ariaLabel="Share and export"
        className="j-btn j-btn--ghost j-btn--icon"
        alignRight
      >
        {close => (
          <>
            {(
              [
                ['Download image (PNG)', props.onDownload],
                ['Copy image', props.onCopyImage],
                ['Copy link to this view', props.onCopyLink],
              ] as const
            ).map(([label, run]) => (
              <button
                key={label}
                type="button"
                className="j-menu__item"
                onClick={() => {
                  close();
                  run();
                }}
              >
                {label}
              </button>
            ))}
          </>
        )}
      </Popover>
      <button
        type="button"
        className="j-btn j-btn--ghost j-btn--icon"
        aria-label={props.fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
        aria-pressed={props.fullscreen}
        onClick={props.onFullscreen}
      >
        {props.fullscreen ? (
          <Minimize2 size={16} aria-hidden="true" />
        ) : (
          <Maximize2 size={16} aria-hidden="true" />
        )}
      </button>
    </div>
  );
}
