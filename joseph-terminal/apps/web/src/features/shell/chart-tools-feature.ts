/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Chart tools feature: screenshot download (with a
 * provenance header), bar replay, session separators, data window and
 * workspace import commands.
 */

import {
  VENUE_NAMES,
  parseInstrumentId,
  type ChartState,
  type FeedState,
} from '@joseph/contracts';
import {DrawingRepository, readImportedDrawings} from '@joseph/drawing-core';
import {
  activeChart,
  paneKey,
  parseWorkspaceFile,
  startReplay,
  type WorkspaceFileParseResult,
} from '@joseph/terminal-shell';
import type {TerminalRuntime} from '../../composition/runtime';
import type {WebFeature} from '../../shell/react-bindings';
import {shellManifest} from './manifest';

/**
 * Applies a parsed workspace file: restores its drawings (each instrument
 * in the file replaces its stored drawings; other instruments keep theirs),
 * replaces the layout, and reloads drawings on open charts whose
 * instrument does not change (charts that switch instrument load the
 * restored drawings themselves).
 * @param runtime Runtime.
 * @param result Parsed file.
 * @return Notification text.
 */
export function importWorkspaceFile(
  runtime: TerminalRuntime,
  result: Extract<WorkspaceFileParseResult, {ok: true}>
): string {
  const current = runtime.workspace.get();
  const workspaceId = current.workspace_id;
  const repository = new DrawingRepository(
    runtime.storage,
    workspaceId,
    runtime.tools
  );
  const imported = result.drawings
    ? readImportedDrawings(result.drawings, {tools: runtime.tools})
    : null;
  if (imported) repository.restore(imported.collections);
  const before = new Map(
    current.charts.map(c => [c.chart_id, c.instrument_id])
  );
  // Keep this browser's workspace id: drawings and settings are stored
  // under it, not under the id in the file.
  runtime.dispatch({
    type: 'replace',
    workspace: {...result.workspace, workspace_id: workspaceId},
  });
  if (imported && imported.collections.length > 0) {
    const restored = new Set(imported.collections.map(c => c.instrumentId));
    const after = runtime.workspace.get();
    for (const [chartId, store] of runtime.drawing.stores) {
      const chart = after.charts.find(c => c.chart_id === chartId);
      if (
        !chart ||
        before.get(chartId) !== chart.instrument_id ||
        !restored.has(chart.instrument_id)
      ) {
        continue;
      }
      if (runtime.drawing.selection.get()?.chartId === chartId) {
        runtime.drawing.selection.set(null);
      }
      store.reset(repository.load(chart.instrument_id));
    }
    // Views keyed on the revision (such as the object list) re-read.
    runtime.drawing.revision.update(n => n + 1);
  }
  const upgraded = result.migrated ? ' (upgraded from an older format)' : '';
  if (!imported) {
    return `Imported the workspace${upgraded}. The file has no drawings, so your drawings were not changed.`;
  }
  const count = imported.drawings;
  const instruments = imported.collections.length;
  const kept =
    imported.opaque > 0
      ? ` ${imported.opaque} drawing${imported.opaque === 1 ? '' : 's'} from a newer version ${imported.opaque === 1 ? 'was' : 'were'} kept as unsupported.`
      : '';
  const dropped =
    imported.dropped > 0
      ? ` ${imported.dropped} unreadable entr${imported.dropped === 1 ? 'y was' : 'ies were'} skipped.`
      : '';
  return `Imported the workspace${upgraded} and ${count} drawing${count === 1 ? '' : 's'} on ${instruments} instrument${instruments === 1 ? '' : 's'}; they replace the drawings saved for ${instruments === 1 ? 'that instrument' : 'those instruments'}.${kept}${dropped}`;
}

/** Height in CSS pixels of the provenance header added to screenshots. */
const HEADER_PX = 28;

/** Provenance words used in screenshot headers. */
const FEED_WORDS: Readonly<Record<FeedState, string>> = {
  LIVE: 'Live data',
  POLL: 'Polled data',
  REPLAY: 'Bar replay',
  STALE: 'Stale data',
  DEMO: 'Demo data',
  UNAVAILABLE: 'Data unavailable',
  CONNECTING: 'Connecting',
};

/**
 * Builds the screenshot header line: symbol, venue, timeframe, provenance
 * and capture time.
 * @param chart Chart state.
 * @param feedState Provenance shown on the chart.
 * @param nowMs Capture time (UTC ms).
 * @return Header text.
 */
export function screenshotCaption(
  chart: ChartState,
  feedState: FeedState,
  nowMs: number
): string {
  const {venue, venueMarketId} = parseInstrumentId(chart.instrument_id);
  const time = new Date(nowMs).toISOString().slice(0, 16).replace('T', ' ');
  return `${venueMarketId} · ${VENUE_NAMES[venue]} · ${chart.timeframe} · ${FEED_WORDS[feedState]} · ${time} UTC · Joseph Terminal`;
}

/**
 * Builds a download file name for a chart screenshot.
 * @param chart Chart state.
 * @param nowMs Capture time (UTC ms).
 * @return File name such as `joseph-BTCUSD-5m-20261122-1504.png`; symbol
 *     characters unsafe in file names become `_`.
 */
export function screenshotFileName(chart: ChartState, nowMs: number): string {
  const {venueMarketId} = parseInstrumentId(chart.instrument_id);
  const stamp = new Date(nowMs)
    .toISOString()
    .slice(0, 16)
    .replace(/[-:]/g, '')
    .replace('T', '-');
  const safe = venueMarketId.replace(/[^A-Za-z0-9._-]/g, '_');
  return `joseph-${safe}-${chart.timeframe}-${stamp}.png`;
}

/**
 * Loads an image from a data URL.
 * @param url Data URL.
 * @return Image, once decoded.
 * @throws `Error` (as a rejection) when the image cannot be decoded.
 */
function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => void resolve(image);
    image.onerror = () =>
      void reject(new Error('Screenshot image failed to load'));
    image.src = url;
  });
}

/**
 * Adds a provenance header above a chart image.
 * @param imageUrl Chart PNG data URL.
 * @param caption Header text.
 * @param colors Background and text colours and the font family.
 * @return Composed PNG data URL, or the original when no 2D canvas context
 *     is available.
 * @throws `Error` (as a rejection) when the chart image cannot be decoded.
 */
export async function composeScreenshot(
  imageUrl: string,
  caption: string,
  colors: {background: string; text: string; font: string}
): Promise<string> {
  const image = await loadImage(imageUrl);
  const canvas = document.createElement('canvas');
  canvas.width = image.width;
  canvas.height = image.height + HEADER_PX;
  const ctx = canvas.getContext('2d');
  if (!ctx) return imageUrl;
  ctx.fillStyle = colors.background;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = colors.text;
  ctx.font = `12px ${colors.font}`;
  ctx.textBaseline = 'middle';
  ctx.fillText(caption, 10, HEADER_PX / 2);
  ctx.drawImage(image, 0, HEADER_PX);
  return canvas.toDataURL('image/png');
}

/**
 * Triggers a browser download.
 * @param url Object or data URL.
 * @param fileName File name.
 */
function download(url: string, fileName: string): void {
  // Some browsers download only from a link that is in the document.
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/**
 * Creates the chart tools feature: commands and hotkeys for screenshots,
 * bar replay, session separators, the data window, fitting the chart and
 * workspace import.
 * @param runtime Terminal runtime.
 * @return Feature.
 */
export function chartToolsFeature(runtime: TerminalRuntime): WebFeature {
  return {
    manifest: shellManifest(
      'shell.chart-tools',
      'Chart tools',
      'Screenshots, bar replay, session separators, data window and workspace import.',
      {uiSlots: ['workspace']}
    ),
    activate(ctx) {
      const active = () => activeChart(runtime.workspace.get());
      const disposers = [
        ctx.commands.register({
          id: 'chart.screenshot',
          title: 'Download a screenshot of the active chart',
          run: async () => {
            const chart = active();
            const port = runtime.charts.get(chart.chart_id);
            const image = port?.screenshot() ?? null;
            if (!image) {
              ctx.notifier.notify(
                'The chart is not ready for a screenshot yet.',
                'warning'
              );
              return;
            }
            const css = getComputedStyle(document.documentElement);
            const now = Date.now();
            const feed =
              runtime.feedStates.get()[chart.chart_id] ?? 'CONNECTING';
            const composed = await composeScreenshot(
              image,
              screenshotCaption(chart, feed, now),
              {
                background: css.getPropertyValue('--hull').trim() || '#0e1c25',
                text: css.getPropertyValue('--chalk').trim() || '#e8eef2',
                font: css.getPropertyValue('--font-ui').trim() || 'sans-serif',
              }
            ).catch(() => image);
            download(composed, screenshotFileName(chart, now));
            ctx.notifier.notify('Screenshot saved', 'success');
          },
        }),
        ctx.commands.register({
          id: 'chart.replay.toggle',
          title: 'Start or stop bar replay on the active chart',
          run: () => {
            const chartId = active().chart_id;
            const key = paneKey({kind: 'chart', chartId});
            if (runtime.detached.get().panes.some(p => p.key === key)) {
              ctx.notifier.notify(
                'This chart is in another window. Start bar replay there.',
                'info'
              );
              return;
            }
            runtime.replay.update(all => {
              if (all[chartId]) {
                const {[chartId]: _ended, ...rest} = all;
                return rest;
              }
              return {...all, [chartId]: startReplay()};
            });
          },
        }),
        ctx.commands.register({
          id: 'chart.sessionSeparators',
          title: 'Show or hide session separators on the active chart',
          run: () =>
            void runtime.dispatch({
              type: 'toggleSessionSeparators',
              chartId: active().chart_id,
            }),
        }),
        ctx.commands.register({
          id: 'chart.dataWindow',
          title: 'Show or hide the data window',
          run: () => void runtime.dataWindow.update(on => !on),
        }),
        ctx.commands.register({
          id: 'chart.fit',
          title: 'Fit all bars on the active chart',
          run: () => runtime.charts.get(active().chart_id)?.fitContent(),
        }),
        ctx.commands.register({
          id: 'workspace.import',
          title: 'Import a workspace from a JSON file',
          run: () => {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = 'application/json,.json';
            input.onchange = () => {
              const file = input.files?.[0];
              if (!file) return;
              void file.text().then(text => {
                const result = parseWorkspaceFile(text, Date.now());
                if (!result.ok) {
                  ctx.notifier.notify(
                    `The workspace couldn’t be imported: ${result.error}`,
                    'error'
                  );
                  return;
                }
                ctx.notifier.notify(
                  importWorkspaceFile(runtime, result),
                  'success'
                );
              });
            };
            input.click();
          },
        }),
        ctx.hotkeys.bind('alt+shift+r', 'chart.replay.toggle'),
        ctx.hotkeys.bind('alt+shift+s', 'chart.screenshot'),
        ctx.hotkeys.bind('alt+0', 'chart.fit'),
      ];
      return () => {
        for (const dispose of disposers.splice(0).reverse()) {
          dispose();
        }
        // Replay belongs to this feature: removing it ends every replay.
        runtime.replay.set({});
      };
    },
  };
}
