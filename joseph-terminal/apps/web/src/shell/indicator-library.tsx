/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Indicator picker: the engine's built-in and plugin
 * indicator scripts and the user's own scripts, with search, catalog
 * categories and a starred collection. Adding a script appends a study to
 * the chart; the chart's studies are listed under Chart options.
 */

'use client';
import type {
  CatalogScript,
  ChartState,
  WorkspaceDocument,
} from '@joseph/contracts';
import {BookOpen, Code2, Layers, Puzzle, Search, Star, X} from 'lucide-react';
import {useEffect, useRef, useState, type ReactElement} from 'react';
import {createPortal} from 'react-dom';
import {LayersMenu} from './layers-menu';
import {attachPython, editPython, pythonState} from './python-state';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {catalogStudy, loadStudyCatalog} from './study-catalog';

/** Workspace panel key storing the user's starred indicator identifiers. */
export const INDICATOR_COLLECTION_KEY = 'shell.indicator-collection';
/** Library sections in navigation order. */
const CATEGORIES = [
  {id: 'builtin', title: 'Built-ins', icon: BookOpen},
  {id: 'plugin', title: 'Plugins', icon: Puzzle},
  {id: 'scripts', title: 'My scripts', icon: Code2},
  {id: 'collection', title: 'My collection', icon: Star},
] as const;
/** Selected library category. */
type Category = (typeof CATEGORIES)[number]['id'];

/**
 * Reads persisted collection identifiers from untrusted workspace panel data.
 * @param workspace Workspace holding the collection.
 * @return Unique indicator identifiers; malformed saved values are ignored.
 */
export function indicatorCollection(workspace: WorkspaceDocument): string[] {
  const raw = (workspace.panels as Record<string, unknown> | undefined)?.[
    INDICATOR_COLLECTION_KEY
  ];
  if (!Array.isArray(raw)) return [];
  return [...new Set(raw.filter((id): id is string => typeof id === 'string'))];
}

/**
 * Renders the toolbar trigger and modal indicator library.
 * @param props Active chart whose indicators are edited.
 * @return Trigger and open dialog, if requested.
 */
export function IndicatorsButton({chart}: {chart: ChartState}): ReactElement {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="j-btn j-btn--ghost"
        data-testid="indicators-menu"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => void setOpen(true)}
      >
        <Layers size={16} aria-hidden="true" />
        <span>Indicators</span>
      </button>
      {open &&
        createPortal(
          <IndicatorLibrary
            chart={chart}
            onClose={() => {
              setOpen(false);
              trigger.current?.focus();
            }}
          />,
          document.body
        )}
    </>
  );
}

/**
 * Renders searchable built-in, custom-plugin and collection lists.
 * @param props Active chart and callback to close and restore trigger focus.
 * @return Modal library with persisted favorites and chart controls.
 */
function IndicatorLibrary({
  chart: requestedChart,
  onClose,
}: {
  chart: ChartState;
  onClose: () => void;
}): ReactElement {
  const runtime = useRuntime();
  const workspace = useStore(runtime.workspace);
  const python = pythonState(runtime);
  const owned = useStore(python.catalog);
  useEffect(() => {
    if (!runtime.config.demo) {
      void python.client.list().then(
        value => void python.catalog.set(value),
        failure => void runtime.notifier.notify(String(failure), 'error')
      );
    }
  }, [runtime, python]);
  const chart =
    workspace.charts.find(item => item.chart_id === requestedChart.chart_id) ??
    requestedChart;
  const favorites = indicatorCollection(workspace);
  const [category, setCategory] = useState<Category>('builtin');
  const [section, setSection] = useState('');
  const [query, setQuery] = useState('');
  const [plugins, setPlugins] = useState<readonly CatalogScript[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    input.current?.focus();
    return () => {
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, []);
  useEffect(() => {
    let alive = true;
    loadStudyCatalog(runtime).then(
      list => {
        if (alive) {
          setPlugins(list);
          setError(null);
        }
      },
      (failure: unknown) => {
        if (alive) {
          setError(
            failure instanceof Error
              ? failure.message
              : 'Could not load indicators'
          );
        }
      }
    );
    return () => {
      alive = false;
    };
  }, [runtime, retry]);
  const sections = [
    ...new Set(
      (plugins ?? [])
        .filter(info => info.origin === category)
        .map(info => info.category)
        .filter(Boolean)
    ),
  ].sort();
  const matches = (plugins ?? []).filter(info => {
    if (category === 'scripts') return false;
    if (category === 'collection') {
      if (!favorites.includes(info.script_id)) return false;
    } else if (info.origin !== category) return false;
    if (section && category !== 'collection' && info.category !== section) {
      return false;
    }
    return `${info.title} ${info.short_title} ${info.script_id} ${info.category} ${info.description}`
      .toLowerCase()
      .includes(query.trim().toLowerCase());
  });
  const onChart = (scriptId: string) =>
    chart.studies.filter(study => study.script_id === scriptId).length;
  const managed = (owned?.scripts ?? []).filter(
    script =>
      ['indicator', 'strategy', 'alert'].includes(script.kind) &&
      (category === 'scripts' || category === 'collection') &&
      (category !== 'collection' ||
        favorites.includes(`python:${script.script_id}`)) &&
      `${script.name} ${script.script_id}`
        .toLowerCase()
        .includes(query.trim().toLowerCase())
  );
  const selectCategory = (next: Category) => {
    setCategory(next);
    setSection('');
    setQuery('');
  };
  return (
    <div
      className="palette-backdrop indicator-library-backdrop"
      onPointerDown={event => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialog}
        className="j-dialog indicator-library"
        role="dialog"
        aria-modal="true"
        aria-label="Indicators"
        onKeyDown={event => {
          event.stopPropagation();
          if (event.key === 'Escape') {
            event.preventDefault();
            onClose();
          }
          if (event.key !== 'Tab') return;
          const nodes = [
            ...(dialog.current?.querySelectorAll<HTMLElement>(
              'button:not(:disabled):not([tabindex="-1"]), input, [tabindex="0"]'
            ) ?? []),
          ];
          const first = nodes[0];
          const last = nodes.at(-1);
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
          }
        }}
      >
        <header className="indicator-library__header">
          <div>
            <h2>Indicators</h2>
            <p>Find a study for your chart</p>
          </div>
          <button
            type="button"
            className="j-btn j-btn--ghost j-btn--icon"
            aria-label="Close indicators"
            onClick={onClose}
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>
        <label className="indicator-library__search">
          <Search size={17} aria-hidden="true" />
          <span className="sr-only">Search indicators</span>
          <input
            ref={input}
            className="j-input"
            placeholder="Search indicators by name or description"
            value={query}
            onChange={event => void setQuery(event.target.value)}
          />
        </label>
        <div className="indicator-library__body">
          <nav
            className="indicator-library__nav"
            role="tablist"
            aria-label="Indicator categories"
            aria-orientation="vertical"
          >
            {CATEGORIES.map((item, index) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  id={`indicator-category-${item.id}`}
                  aria-controls="indicator-results"
                  aria-selected={category === item.id}
                  tabIndex={category === item.id ? 0 : -1}
                  onClick={() => void selectCategory(item.id)}
                  onKeyDown={event => {
                    let next: number;
                    if (
                      event.key === 'ArrowDown' ||
                      event.key === 'ArrowRight'
                    ) {
                      next = (index + 1) % CATEGORIES.length;
                    } else if (
                      event.key === 'ArrowUp' ||
                      event.key === 'ArrowLeft'
                    ) {
                      next =
                        (index + CATEGORIES.length - 1) % CATEGORIES.length;
                    } else return;
                    event.preventDefault();
                    const target = CATEGORIES[next];
                    if (target) {
                      selectCategory(target.id);
                      document
                        .getElementById(`indicator-category-${target.id}`)
                        ?.focus();
                    }
                  }}
                >
                  <Icon size={16} aria-hidden="true" />
                  {item.title}
                  {item.id === 'collection' && <span>{favorites.length}</span>}
                </button>
              );
            })}
          </nav>
          <section
            id="indicator-results"
            className="indicator-library__results"
            role="tabpanel"
            aria-labelledby={`indicator-category-${category}`}
          >
            {sections.length > 1 && (
              <label className="indicator-library__section">
                <span className="sr-only">Category</span>
                <select
                  className="j-select"
                  aria-label="Indicator category"
                  value={section}
                  onChange={event => void setSection(event.target.value)}
                >
                  <option value="">All categories</option>
                  {sections.map(name => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {error ? (
              <div className="j-empty" role="alert">
                <p>{error}</p>
                <button
                  type="button"
                  className="j-btn"
                  onClick={() => void setRetry(value => value + 1)}
                >
                  Retry
                </button>
              </div>
            ) : plugins === null ? (
              <p className="j-empty" role="status">
                Loading indicators…
              </p>
            ) : matches.length === 0 && managed.length === 0 ? (
              <div className="j-empty">
                <h3>
                  {query
                    ? 'No indicators found'
                    : category === 'scripts'
                      ? 'No scripts yet'
                      : category === 'plugin'
                        ? 'No plugins installed'
                        : 'Your collection is empty'}
                </h3>
                <p>
                  {query
                    ? 'Try a different name or description.'
                    : category === 'scripts'
                      ? 'Indicators you write in the Python editor appear here.'
                      : category === 'plugin'
                        ? 'Indicator packages the operator installs appear here.'
                        : 'Star an indicator to save it to your collection.'}
                </p>
              </div>
            ) : (
              matches.map(info => {
                const saved = favorites.includes(info.script_id);
                const count = onChart(info.script_id);
                return (
                  <div
                    key={info.script_id}
                    className="indicator-library__entry"
                  >
                    <button
                      type="button"
                      className={`j-btn j-btn--ghost j-btn--icon indicator-library__star ${saved ? 'indicator-library__star--saved' : ''}`}
                      aria-label={`Collect ${info.title}`}
                      aria-pressed={saved}
                      title="Save to My collection"
                      onClick={() =>
                        void runtime.dispatch({
                          type: 'setPanel',
                          featureId: INDICATOR_COLLECTION_KEY,
                          state: saved
                            ? favorites.filter(id => id !== info.script_id)
                            : [...favorites, info.script_id],
                        })
                      }
                    >
                      <Star
                        size={16}
                        fill={saved ? 'currentColor' : 'none'}
                        aria-hidden="true"
                      />
                    </button>
                    <div className="indicator-library__study">
                      <strong>
                        {info.title}{' '}
                        <span className="j-muted">{info.short_title}</span>
                      </strong>
                      <p>
                        {[
                          info.category,
                          info.origin === 'plugin' ? info.package : null,
                          count ? `${count} on chart` : null,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                      </p>
                      <p>{info.description}</p>
                      <button
                        type="button"
                        className="j-btn"
                        data-testid={`add-study-${info.script_id}`}
                        onClick={() =>
                          void runtime.dispatch({
                            type: 'addStudy',
                            chartId: chart.chart_id,
                            study: catalogStudy(chart, info),
                          })
                        }
                      >
                        Add {info.short_title || info.title}
                      </button>
                    </div>
                  </div>
                );
              })
            )}
            {managed.map(script => (
              <div
                key={`python:${script.script_id}`}
                className="indicator-library__entry"
              >
                <button
                  type="button"
                  className="j-btn j-btn--ghost j-btn--icon"
                  aria-label={`Collect ${script.name}`}
                  aria-pressed={favorites.includes(
                    `python:${script.script_id}`
                  )}
                  onClick={() =>
                    void runtime.dispatch({
                      type: 'setPanel',
                      featureId: INDICATOR_COLLECTION_KEY,
                      state: favorites.includes(`python:${script.script_id}`)
                        ? favorites.filter(
                            id => id !== `python:${script.script_id}`
                          )
                        : [...favorites, `python:${script.script_id}`],
                    })
                  }
                >
                  <Star size={16} aria-hidden="true" />
                </button>
                <div className="indicator-library__study">
                  <strong>{script.name}</strong>
                  <p>
                    {script.kind} · Python · v{script.version}
                  </p>
                  <button
                    type="button"
                    className="j-btn"
                    disabled={!owned?.runtime_enabled}
                    onClick={() => void attachPython(runtime, chart, script)}
                  >
                    Add {script.name}
                  </button>
                  <button
                    type="button"
                    className="j-btn"
                    onClick={() => {
                      void editPython(
                        runtime,
                        script.script_id,
                        script.version
                      ).then(
                        onClose,
                        failure =>
                          void runtime.notifier.notify(String(failure), 'error')
                      );
                    }}
                  >
                    Edit source
                  </button>
                </div>
              </div>
            ))}
            {category === 'scripts' && owned?.next_offset != null && (
              <button
                className="j-btn"
                onClick={() =>
                  void python.client.list(owned.next_offset ?? 0).then(
                    next =>
                      void python.catalog.set({
                        ...next,
                        scripts: [...owned.scripts, ...next.scripts],
                      }),
                    failure =>
                      void runtime.notifier.notify(String(failure), 'error')
                  )
                }
              >
                Load more scripts
              </button>
            )}
            {category === 'scripts' && (
              <button
                type="button"
                className="j-btn"
                onClick={() => {
                  void runtime.commands
                    .execute('python.editor')
                    .catch(() => undefined);
                  onClose();
                }}
              >
                Open Python editor
              </button>
            )}
          </section>
        </div>
        <details className="indicator-library__options" open>
          <summary>Chart options</summary>
          <LayersMenu chart={chart} />
        </details>
      </div>
    </div>
  );
}
