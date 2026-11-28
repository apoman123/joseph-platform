/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Heatmap tab: stocks, ETFs and crypto coins as a squarified
 * treemap sized and coloured by the chosen metrics.
 *
 * Tiles are absolutely positioned elements laid out in CSS pixels from a
 * ResizeObserver measurement, so text is never stretched. Pointer and key
 * events are delegated to the map; hovering only re-renders the hover
 * outline and card, so 1000 tiles stay smooth. "Download image" redraws the
 * same layout onto a canvas. A click opens a candlestick tab of the tile's
 * instrument; a right click offers the chart, the watchlist and zooming.
 */

'use client';
import type {ChartState} from '@joseph/contracts';
import {encodeShareLink} from '@joseph/terminal-shell';
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactElement,
} from 'react';
import type {TerminalRuntime} from '../../composition/runtime';
import {ids} from '../ids';
import {useStore} from '../react-bindings';
import {buildLegend, scheme as schemeOf, segmentOf, textColor} from './colors';
import {useHeatmapData} from './data';
import styles from './heatmap.module.css';
import {HeatmapLegend, MISSING_SEGMENT} from './legend';
import {
  chooseGrouping,
  chooseMetric,
  formatMetric,
  metricValue,
  peersOf,
  prepareView,
  readSettings,
  type HeatmapSettings,
  type PreparedTile,
} from './model';
import {
  badgeLetter,
  fitTileText,
  scenePng,
  type DrawnTile,
  type ExportScene,
} from './render';
import {HeatmapToolbar} from './toolbar';
import {HeatmapTooltip} from './tooltip';
import {layoutHeatmap} from './treemap';

/** Command id of the watchlist's "add instrument" command. */
const WATCHLIST_ADD = 'watchlist.add';

/** Legend state before any segment is toggled. */
const NOTHING_HIDDEN: ReadonlySet<number> = new Set();

/** Hover or keyboard focus on a tile. */
interface Hover {
  /** Index into the prepared tiles. */
  index: number;
  /** Anchor relative to the map. */
  x: number;
  /** Anchor relative to the map. */
  y: number;
}

/**
 * Measures an element's content box with a ResizeObserver.
 * @return Ref callback target and the measured size.
 */
function useSize(): [
  (node: HTMLDivElement | null) => void,
  {w: number; h: number},
] {
  const [size, setSize] = useState({w: 0, h: 0});
  const observer = useRef<ResizeObserver | null>(null);
  const ref = useCallback((node: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!node) return;
    const measure = () => {
      const rect = node.getBoundingClientRect();
      const w = Math.floor(rect.width);
      const h = Math.floor(rect.height);
      // Compare the stored (floored) size so fractional layout widths
      // cannot trigger a new state object on every measurement.
      setSize(prev => (prev.w === w && prev.h === h ? prev : {w, h}));
    };
    measure();
    if (typeof ResizeObserver !== 'undefined') {
      observer.current = new ResizeObserver(measure);
      observer.current.observe(node);
    }
  }, []);
  return [ref, size];
}

/**
 * Renders a saved heatmap tab.
 * @param props Chart tab, whether it is active, and the terminal runtime.
 * @return Heatmap with toolbar, legend, map and provenance footer.
 */
export function Heatmap({
  chart,
  active,
  runtime,
}: {
  chart: ChartState;
  active: boolean;
  runtime: TerminalRuntime;
}): ReactElement {
  const workspace = useStore(runtime.workspace);
  const panelKey = `heatmap.${chart.chart_id}`;
  const saved = (workspace.panels as Record<string, unknown> | undefined)?.[
    panelKey
  ];
  const settings = useMemo(() => readSettings(saved), [saved]);
  const save = useCallback(
    (patch: Partial<HeatmapSettings>) =>
      void runtime.dispatch({
        type: 'setPanel',
        featureId: panelKey,
        state: {...settings, ...patch},
      }),
    [runtime, panelKey, settings]
  );
  const data = useHeatmapData(runtime, settings.type, settings.dataset);
  const snapshot = data.snapshot;
  const [mapRef, size] = useSize();
  const section = useRef<HTMLElement>(null);
  const tilesRef = useRef<HTMLDivElement>(null);
  // A stable ref callback: an inline one would detach and re-measure on
  // every render.
  const attachMap = useCallback(
    (node: HTMLDivElement | null) => {
      mapRef(node);
      tilesRef.current = node;
    },
    [mapRef]
  );
  const [hover, setHover] = useState<Hover | null>(null);
  const [menu, setMenu] = useState<Hover | null>(null);
  const [hiddenState, setHiddenState] = useState<{
    key: string;
    hidden: ReadonlySet<number>;
  }>({key: '', hidden: new Set()});
  const [fullscreen, setFullscreen] = useState(false);
  const tooltipId = useId();

  const sizeMetric = snapshot
    ? chooseMetric(snapshot.size_metrics, settings.size, snapshot.default_size)
    : undefined;
  const colorMetric = snapshot
    ? chooseMetric(
        snapshot.color_metrics,
        settings.color,
        snapshot.default_color
      )
    : undefined;
  const grouping = snapshot
    ? chooseGrouping(snapshot, settings.grouping)
    : 'none';
  const legendKey = `${snapshot?.dataset ?? ''}|${colorMetric?.id ?? ''}`;
  const hidden =
    hiddenState.key === legendKey ? hiddenState.hidden : NOTHING_HIDDEN;
  const colors = schemeOf(settings.scheme);

  const view = useMemo(
    () =>
      snapshot && sizeMetric && colorMetric
        ? prepareView(snapshot, {
            size: sizeMetric.id,
            defaultSize: snapshot.default_size,
            color: colorMetric.id,
            grouping,
            zoom: settings.zoom,
          })
        : null,
    [snapshot, sizeMetric, colorMetric, grouping, settings.zoom]
  );
  const legend = useMemo(
    () =>
      buildLegend(
        (view?.tiles ?? [])
          .map(row => row.color)
          .filter((value): value is number => value !== null),
        colorMetric?.scale === 'sequential' ? 'sequential' : 'diverging',
        colors,
        colorMetric?.format === 'percent'
      ),
    [view, colorMetric, colors]
  );
  const layout = useMemo(
    () =>
      view && size.w > 0 && size.h > 0
        ? layoutHeatmap(view.groups, {x: 0, y: 0, w: size.w, h: size.h})
        : {groups: [], tiles: []},
    [view, size.w, size.h]
  );
  const currency = snapshot?.currency ?? '';
  const fillOf = useCallback(
    (row: PreparedTile) => {
      const segment = segmentOf(legend, row.color);
      return segment === null
        ? colors.missing
        : (legend.segments[segment]?.color ?? colors.missing);
    },
    [legend, colors]
  );
  const drawn: DrawnTile[] = useMemo(
    () =>
      layout.tiles.map(placed => {
        const row = view?.tiles[placed.index];
        if (!row) {
          return {
            box: placed.box,
            fill: colors.missing,
            ink: '#ffffff',
            text: fitTileText(placed.box, null, null, false),
            letter: '?',
            hidden: true,
          };
        }
        const segment = segmentOf(legend, row.color);
        const fill = fillOf(row);
        const title =
          settings.title === 'none'
            ? null
            : settings.title === 'description'
              ? (row.tile.name ?? row.tile.symbol)
              : row.tile.symbol;
        const value =
          settings.value === 'color'
            ? formatMetric(colorMetric, row.color, currency)
            : settings.value === 'price'
              ? (row.tile.price ?? '—')
              : settings.value === 'size' && sizeMetric?.id !== 'mono'
                ? formatMetric(
                    sizeMetric,
                    metricValue(row.tile, sizeMetric?.id ?? ''),
                    currency
                  )
                : null;
        return {
          box: placed.box,
          fill,
          ink: textColor(fill),
          text: fitTileText(placed.box, title, value, settings.logo),
          letter: badgeLetter(row.tile.symbol),
          hidden: hidden.has(segment ?? MISSING_SEGMENT),
        };
      }),
    [
      layout,
      view,
      legend,
      fillOf,
      settings.title,
      settings.value,
      settings.logo,
      colorMetric,
      sizeMetric,
      currency,
      hidden,
      colors,
    ]
  );

  const openChart = useCallback(
    (row: PreparedTile | undefined) => {
      const instrumentId = row?.tile.instrument_id;
      if (!instrumentId) return;
      runtime.dispatch({
        type: 'addChart',
        chartId: `chart-${ids.next()}`,
        instrumentId: instrumentId,
        viewType: 'candles',
      });
    },
    [runtime]
  );
  const addToWatchlist = useCallback(
    (row: PreparedTile | undefined) => {
      const instrumentId = row?.tile.instrument_id;
      if (!instrumentId) return;
      runtime.commands
        .execute(WATCHLIST_ADD, {instrumentId})
        .catch(
          () =>
            void runtime.notifier.notify(
              'The watchlist is not available.',
              'error'
            )
        );
    },
    [runtime]
  );

  const tileElements = useMemo(
    () =>
      drawn.map((tile, index) => {
        const row = view?.tiles[layout.tiles[index]?.index ?? -1];
        const style = {
          left: tile.box.x,
          top: tile.box.y,
          width: tile.box.w,
          height: tile.box.h,
        };
        if (!row || tile.hidden) {
          return (
            <div
              key={row?.tile.key ?? index}
              className={styles.hiddenTile}
              style={style}
              aria-hidden="true"
            />
          );
        }
        // The accessible name starts with the visible text (WCAG 2.5.3).
        const visible = [tile.text.title, tile.text.value]
          .filter(Boolean)
          .join(' ');
        const label = [
          visible,
          visible.startsWith(row.tile.symbol) ? null : row.tile.symbol,
          row.tile.name,
          `${colorMetric?.label ?? ''} ${formatMetric(colorMetric, row.color, currency)}`,
          row.tile.instrument_id ? null : 'no chart available',
        ]
          .filter(Boolean)
          .join(', ');
        return (
          <div
            key={row.tile.key}
            role="button"
            tabIndex={index === 0 ? 0 : -1}
            data-tile={index}
            className={styles.tile}
            style={{...style, background: tile.fill, color: tile.ink}}
            aria-label={label}
            aria-disabled={row.tile.instrument_id ? undefined : true}
          >
            {tile.text.badge > 0 && (
              <span
                className={styles.badge}
                style={{
                  width: tile.text.badge,
                  height: tile.text.badge,
                  fontSize: Math.round(tile.text.badge * 0.55),
                }}
                aria-hidden="true"
              >
                {tile.letter}
              </span>
            )}
            {tile.text.title !== null && (
              <span
                className={styles.tileTitle}
                style={{fontSize: tile.text.titleSize}}
              >
                {tile.text.title}
              </span>
            )}{' '}
            {tile.text.value !== null && (
              <span
                className={styles.tileValue}
                style={{fontSize: tile.text.valueSize}}
              >
                {tile.text.value}
              </span>
            )}
          </div>
        );
      }),
    [drawn, view, layout, colorMetric, currency]
  );

  /**
   * Finds the prepared tile of a delegated event target.
   * @param target Event target.
   * @return Drawn-tile position and prepared tile, or null.
   */
  const tileAt = (
    target: EventTarget | null
  ): {position: number; row: PreparedTile} | null => {
    const node = (target as HTMLElement | null)?.closest?.('[data-tile]');
    const position = Number(node?.getAttribute('data-tile') ?? NaN);
    const row = view?.tiles[layout.tiles[position]?.index ?? -1];
    return Number.isInteger(position) && row ? {position, row} : null;
  };
  const anchorOf = (position: number): Hover | null => {
    const placed = layout.tiles[position];
    if (!placed) return null;
    return {
      index: placed.index,
      x: placed.box.x + placed.box.w / 2,
      y: placed.box.y + Math.min(placed.box.h, 24),
    };
  };
  const pointer = (event: ReactMouseEvent) => {
    const rect = tilesRef.current?.getBoundingClientRect();
    return {
      x: event.clientX - (rect?.left ?? 0),
      y: event.clientY - (rect?.top ?? 0),
    };
  };
  const onPointerMove = (event: ReactMouseEvent) => {
    const hit = tileAt(event.target);
    if (!hit) {
      if (hover) setHover(null);
      return;
    }
    const placed = layout.tiles[hit.position];
    setHover({index: placed?.index ?? -1, ...pointer(event)});
  };
  const focusTile = (position: number) => {
    const node = tilesRef.current?.querySelector<HTMLElement>(
      `[data-tile="${position}"]`
    );
    node?.focus();
  };
  const onKeyDown = (event: ReactKeyboardEvent) => {
    const hit = tileAt(event.target);
    if (!hit) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      openChart(hit.row);
    } else if (
      event.key === 'ContextMenu' ||
      (event.key === 'F10' && event.shiftKey)
    ) {
      event.preventDefault();
      setMenu(anchorOf(hit.position));
    } else if (/^Arrow(Right|Down|Left|Up)$/.test(event.key)) {
      event.preventDefault();
      const step = /Right|Down/.test(event.key) ? 1 : -1;
      let next = hit.position + step;
      while (drawn[next]?.hidden) {
        next += step;
      }
      if (next >= 0 && next < drawn.length) focusTile(next);
    } else if (event.key === 'Escape') {
      setMenu(null);
    }
  };
  const menuRow = menu ? view?.tiles[menu.index] : undefined;

  useEffect(() => {
    const onChange = () =>
      void setFullscreen(document.fullscreenElement === section.current);
    document.addEventListener('fullscreenchange', onChange);
    return () =>
      void document.removeEventListener('fullscreenchange', onChange);
  }, []);
  useEffect(() => {
    if (!menu) return undefined;
    const close = (event: Event) => {
      if (
        event instanceof KeyboardEvent
          ? event.key === 'Escape'
          : !(event.target as HTMLElement | null)?.closest?.(
              '[data-heatmap-menu]'
            )
      ) {
        setMenu(null);
      }
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', close);
    };
  }, [menu]);

  const scene = (): ExportScene => {
    const font =
      typeof getComputedStyle === 'function' && section.current
        ? getComputedStyle(section.current).fontFamily
        : 'sans-serif';
    return {
      width: size.w,
      height: size.h,
      title: [
        snapshot?.demo ? 'DEMO' : null,
        snapshot?.dataset_label,
        settings.zoom,
        sizeMetric?.label,
        colorMetric?.label,
      ]
        .filter(Boolean)
        .join(' · '),
      footer: [
        snapshot
          ? `As of ${new Date(snapshot.generated_at_ms).toISOString()}`
          : null,
        ...(snapshot?.provenance ?? []).map(
          row => `${row.field}: ${row.source}`
        ),
      ]
        .filter(Boolean)
        .join(' · '),
      groups: layout.groups.map(group => ({
        box: group.box,
        header: group.header,
        name: group.name,
      })),
      tiles: drawn,
      font,
      background: '#0a0a0b',
      chrome: '#e8ecee',
    };
  };
  const fileName = () =>
    `heatmap-${snapshot?.dataset ?? 'view'}-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.png`;
  const download = async () => {
    try {
      const blob = await scenePng(scene());
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = fileName();
      link.click();
      URL.revokeObjectURL(url);
    } catch (failure) {
      runtime.notifier.notify(
        failure instanceof Error ? failure.message : 'Image export failed',
        'error'
      );
    }
  };
  const copyImage = async () => {
    try {
      if (typeof ClipboardItem === 'undefined' || !navigator.clipboard?.write) {
        throw new Error(
          'This browser cannot copy images; download it instead.'
        );
      }
      await navigator.clipboard.write([
        new ClipboardItem({'image/png': scenePng(scene())}),
      ]);
      runtime.notifier.notify('Heatmap image copied.', 'success');
    } catch (failure) {
      runtime.notifier.notify(
        failure instanceof Error ? failure.message : 'Copy failed',
        'error'
      );
    }
  };
  const copyLink = async () => {
    const doc = {...runtime.workspace.get(), active_chart_id: chart.chart_id};
    const url = `${window.location.origin}${window.location.pathname}#ws=${encodeShareLink(doc)}`;
    try {
      await navigator.clipboard.writeText(url);
      runtime.notifier.notify(
        'Link copied. It opens this heatmap with the same settings.',
        'success'
      );
    } catch {
      runtime.notifier.notify(
        'Copy failed. Allow clipboard access and try again.',
        'error'
      );
    }
  };
  const toggleFullscreen = () => {
    if (document.fullscreenElement) {
      void document.exitFullscreen?.().catch(() => undefined);
    } else {
      void section.current?.requestFullscreen?.().catch(() => undefined);
    }
  };

  const hoverRow = hover ? view?.tiles[hover.index] : undefined;
  const hoverBox = hoverRow
    ? layout.tiles.find(row => row.index === hover?.index)?.box
    : undefined;
  const groupWord = grouping === 'asset_class' ? 'asset classes' : 'sectors';
  const asOf = snapshot ? new Date(snapshot.generated_at_ms) : null;
  return (
    <section
      ref={section}
      className={styles.heatmap}
      aria-label="Heatmap"
      onPointerDown={() => {
        if (!active) {
          runtime.dispatch({type: 'setActiveChart', chartId: chart.chart_id});
        }
      }}
    >
      <HeatmapToolbar
        catalog={data.catalog}
        settings={settings}
        dataset={snapshot?.dataset ?? settings.dataset ?? ''}
        datasetLabel={snapshot?.dataset_label ?? 'Loading…'}
        sizes={snapshot?.size_metrics ?? []}
        colors={snapshot?.color_metrics ?? []}
        groupings={snapshot?.groupings ?? []}
        size={sizeMetric?.id ?? ''}
        color={colorMetric?.id ?? ''}
        grouping={grouping}
        onChange={save}
        onRefresh={data.refresh}
        loading={data.loading}
        onDownload={() => void download()}
        onCopyImage={() => void copyImage()}
        onCopyLink={() => void copyLink()}
        fullscreen={fullscreen}
        onFullscreen={toggleFullscreen}
      />
      <div className={styles.subbar}>
        {settings.zoom !== null && view && grouping !== 'none' ? (
          <nav className={styles.breadcrumb} aria-label="Heatmap zoom">
            <button
              type="button"
              className="j-btn j-btn--sm"
              onClick={() => void save({zoom: null})}
            >
              All {groupWord}
            </button>
            <span aria-hidden="true">›</span>
            <strong aria-current="page">{settings.zoom}</strong>
          </nav>
        ) : (
          <span className={styles.hint}>
            {grouping !== 'none'
              ? `Click a ${grouping === 'asset_class' ? 'asset class' : 'sector'} title to zoom in`
              : ''}
          </span>
        )}
        {colorMetric && view && (
          <HeatmapLegend
            legend={legend}
            hidden={hidden}
            missing={colors.missing}
            metricLabel={colorMetric.label}
            onToggle={segment => {
              const next = new Set(hidden);
              if (next.has(segment)) next.delete(segment);
              else next.add(segment);
              setHiddenState({key: legendKey, hidden: next});
            }}
          />
        )}
      </div>
      <div className={styles.mapWrap}>
        <div
          ref={attachMap}
          className={styles.map}
          role="group"
          aria-label={`${snapshot?.dataset_label ?? 'Heatmap'} tiles`}
          onPointerMove={onPointerMove}
          onPointerLeave={() => void setHover(null)}
          onClick={event => void openChart(tileAt(event.target)?.row)}
          onContextMenu={event => {
            const hit = tileAt(event.target);
            if (!hit) return;
            event.preventDefault();
            setMenu({
              index: layout.tiles[hit.position]?.index ?? -1,
              ...pointer(event),
            });
          }}
          onKeyDown={onKeyDown}
          onFocus={event => {
            const hit = tileAt(event.target);
            if (hit) setHover(anchorOf(hit.position));
          }}
          onBlur={() => void setHover(null)}
        >
          {!snapshot ? (
            <p className={styles.status} role={data.error ? 'alert' : 'status'}>
              {data.error ?? 'Loading heatmap…'}
            </p>
          ) : !view?.tiles.length ? (
            <p className={styles.status} role="status">
              {snapshot.tiles.length
                ? `No symbol has a value for ${sizeMetric?.label ?? 'this size'}; choose another Size by.`
                : 'No symbols available in this data set.'}
            </p>
          ) : null}
          {layout.groups.map(group =>
            group.header && group.name !== null ? (
              <button
                key={`h-${group.name}`}
                type="button"
                className={styles.groupHeader}
                style={{
                  left: group.header.x,
                  top: group.header.y,
                  width: group.header.w,
                  height: group.header.h,
                }}
                title={
                  settings.zoom === null
                    ? `Zoom into ${group.name}`
                    : `Back to all ${groupWord}`
                }
                onClick={event => {
                  event.stopPropagation();
                  save({zoom: settings.zoom === null ? group.name : null});
                }}
              >
                {group.name}
              </button>
            ) : null
          )}
          {tileElements}
          {hoverBox && (
            <div
              className={styles.hoverRing}
              style={{
                left: hoverBox.x,
                top: hoverBox.y,
                width: hoverBox.w,
                height: hoverBox.h,
              }}
              aria-hidden="true"
            />
          )}
        </div>
        {hover && hoverRow && !menu && (
          <HeatmapTooltip
            id={tooltipId}
            target={hoverRow}
            peers={peersOf(view?.tiles ?? [], hoverRow)}
            fillOf={fillOf}
            size={sizeMetric}
            color={colorMetric}
            currency={currency}
            x={hover.x}
            y={hover.y}
            width={size.w}
            height={size.h}
          />
        )}
        {menu && menuRow && (
          <div
            className={`${styles.contextMenu} j-menu`}
            role="menu"
            aria-label={`${menuRow.tile.symbol} actions`}
            data-heatmap-menu=""
            style={{
              left: Math.min(menu.x, Math.max(0, size.w - 220)),
              top: Math.min(menu.y, Math.max(0, size.h - 130)),
            }}
          >
            <button
              type="button"
              role="menuitem"
              className="j-menu__item"
              autoFocus
              disabled={!menuRow.tile.instrument_id}
              onClick={() => {
                openChart(menuRow);
                setMenu(null);
              }}
            >
              Open chart
            </button>
            <button
              type="button"
              role="menuitem"
              className="j-menu__item"
              disabled={!menuRow.tile.instrument_id}
              onClick={() => {
                addToWatchlist(menuRow);
                setMenu(null);
              }}
            >
              Add to watchlist
            </button>
            {menuRow.group !== null && (
              <button
                type="button"
                role="menuitem"
                className="j-menu__item"
                onClick={() => {
                  save({zoom: settings.zoom === null ? menuRow.group : null});
                  setMenu(null);
                }}
              >
                {settings.zoom === null
                  ? `Zoom into ${menuRow.group}`
                  : `Back to all ${groupWord}`}
              </button>
            )}
            {!menuRow.tile.instrument_id && (
              <p className={styles.menuNote}>
                {menuRow.tile.chart_unavailable_reason ??
                  'No chart on configured venues'}
              </p>
            )}
          </div>
        )}
      </div>
      <footer className={styles.footer}>
        <p>
          {snapshot?.demo && (
            <span className="j-badge j-badge--demo">DEMO</span>
          )}{' '}
          {snapshot && view
            ? `${view.tiles.length} of ${snapshot.universe_count} symbols · ${snapshot.priced_count} priced`
            : ''}
          {view && view.withoutSize > 0
            ? ` · ${view.withoutSize} without ${sizeMetric?.label ?? 'a size value'} not shown`
            : ''}
          {snapshot ? ` · ${snapshot.currency}` : ''}
          {asOf ? (
            <>
              {' · as of '}
              <time dateTime={asOf.toISOString()}>
                {asOf.toLocaleTimeString()}
              </time>
            </>
          ) : null}
          {snapshot?.pending ? ' · loading more data…' : ' · auto refresh 60 s'}
        </p>
        {data.error && snapshot && <p role="alert">{data.error}</p>}
        {(snapshot?.provenance ?? []).map(row => (
          <p key={`${row.field}-${row.source}`}>
            {row.field}: {row.source}
            {row.as_of_ms
              ? ` (${new Date(row.as_of_ms).toISOString().slice(0, 16).replace('T', ' ')} UTC)`
              : ''}
            {row.note ? ` — ${row.note}` : ''}
          </p>
        ))}
        {Object.entries(snapshot?.source_errors ?? {}).map(
          ([source, message]) => (
            <p key={source} role="status" className={styles.sourceError}>
              {source}: {message}
            </p>
          )
        )}
      </footer>
    </section>
  );
}
