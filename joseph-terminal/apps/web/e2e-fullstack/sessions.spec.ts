/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Browser → real market engine on session calendars (XNYS is
 * the NYSE calendar). Runs with `playwright.fullstack.config.ts`.
 *
 * It relies on these market-engine features:
 *
 * - `GET /v1/calendars/{calendar_id}/sessions?from_ms&to_ms` → contract
 *   `SessionList` (windows up to ~400 days).
 * - The DEMO instrument `mock:stock:ACME` on the mock venue, on the XNYS
 *   calendar (`Instrument.calendar = "XNYS"`), with session-aligned candles
 *   (`/v1/candles`), `taker_buy_method: "classified"` bars and the
 *   order-flow series publishing `method: "trade_classification"`.
 * - Anchored VWAP (volume-weighted average price) and regression from
 *   anchors up to 50,000 bars back, and a 422 refusal with a message beyond
 *   that.
 */

import {
  EXCHANGE_HOURS,
  SessionCalendar,
  type Candle,
  type Instrument,
  type SessionList,
} from '@joseph/contracts';
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from '@playwright/test';
import {API, TOKEN} from '../playwright.fullstack.config';
import {firstCanvas, showDataWindow} from '../e2e/support';
import {loadTerminal} from '../e2e/trading-helpers';

/** Base URL of the market engine under test. */
const MARKET = API.NEXT_PUBLIC_MARKET_API_URL;

/** One day in milliseconds. */
const DAY = 86_400_000;

/**
 * Loads the terminal and charts ACME from the engine's DEMO venue, waiting
 * for its candles.
 * @param page Page.
 */
async function loadAcme(page: Page): Promise<void> {
  await loadTerminal(page);
  await page.getByTestId('instrument-selector').click();
  await page.getByLabel('Search instruments').fill('ACME');
  await page.getByRole('option', {name: /ACME/}).first().click();
  await expect(
    page.getByTestId('chart-pane-chart-1').locator('.pane__symbol')
  ).toHaveText(/^ACME\b/);
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
}

test('the engine serves XNYS sessions and session-aligned ACME bars', async ({
  request,
}) => {
  const now = Date.now();
  const from = now - 60 * DAY;
  const to = now + 14 * DAY;
  const response = await request.get(`${MARKET}/v1/calendars/XNYS/sessions`, {
    params: {from_ms: from, to_ms: to},
  });
  expect(response.ok()).toBeTruthy();
  const list = (await response.json()) as SessionList;
  expect(list.calendar).toBe('XNYS');
  expect(list.sessions.length).toBeGreaterThan(30);
  for (const [i, session] of list.sessions.entries()) {
    expect(session.close_ms).toBeGreaterThan(session.open_ms);
    if (i > 0) {
      expect(session.open_ms).toBeGreaterThan(
        list.sessions[i - 1]?.close_ms ?? 0
      );
    }
  }
  // 24x7 has no sessions; an engine may also refuse the request for it.
  const crypto = await request.get(`${MARKET}/v1/calendars/24x7/sessions`, {
    params: {from_ms: from, to_ms: to},
  });
  if (crypto.ok()) {
    expect(((await crypto.json()) as SessionList).sessions).toEqual([]);
  }

  const instruments = await request.get(`${MARKET}/v1/instruments`, {
    params: {venue: 'mock', q: 'ACME'},
  });
  const acme = (
    (await instruments.json()) as {instruments: Instrument[]}
  ).instruments.find(i => i.instrument_id === 'mock:stock:ACME');
  expect(acme?.calendar).toBe('XNYS');

  // Every bar starts on the session grid and ends where the terminal's
  // calendar (built from the engine's own session list) says it ends.
  const calendar = new SessionCalendar(EXCHANGE_HOURS.XNYS, {
    sessions: list.sessions,
    coverage: [{fromMs: list.from_ms, toMs: list.to_ms}],
  });
  const candles = await request.get(`${MARKET}/v1/candles`, {
    params: {instrument_id: 'mock:stock:ACME', tf: '1h', limit: 60},
  });
  expect(candles.ok()).toBeTruthy();
  const bars = ((await candles.json()) as {candles: Candle[]}).candles;
  expect(bars.length).toBeGreaterThan(10);
  for (const bar of bars) {
    if (bar.start_ms < list.from_ms) continue;
    expect(calendar.isAligned('1h', bar.start_ms)).toBe(true);
    expect(bar.end_ms).toBe(calendar.endOf('1h', bar.start_ms));
    expect(calendar.isOpen(bar.start_ms)).toBe(true);
  }
  for (let i = 1; i < bars.length; i += 1) {
    const previous = bars[i - 1];
    const bar = bars[i];
    if (!previous || !bar || previous.start_ms < list.from_ms) continue;
    expect(calendar.shift('1h', previous.start_ms, 1)).toBe(bar.start_ms);
  }
});

test('the terminal loads sessions, labels exchange time and leaves no gaps', async ({
  page,
}) => {
  // Listen before loading so the terminal's session request is not missed.
  const sessions = page.waitForResponse(
    response =>
      response.url().startsWith(`${MARKET}/v1/calendars/XNYS/sessions`) &&
      response.ok(),
    {timeout: 60_000}
  );
  await loadAcme(page);
  const list = (await (await sessions).json()) as SessionList;
  expect(list.sessions.length).toBeGreaterThan(0);
  const pane = page.getByTestId('chart-pane-chart-1');
  await expect(pane.getByTestId('market-status')).toBeVisible();
  await page
    .getByRole('navigation', {name: 'Timeframe'})
    .getByRole('button', {name: '1h', exact: true})
    .click();
  await expect(page.getByText('Loading candles…')).toHaveCount(0);
  await showDataWindow(page);
  const box = await firstCanvas(page).boundingBox();
  if (!box) throw new Error('no chart');
  const time = page.getByTestId('data-window').locator('.data-window__time');
  // Sweep the crosshair in 3 px steps and collect the distinct bar times
  // the data window shows: all must be NYSE session bars. The 1h load can
  // start after the loading check above passes, so retry the sweep until
  // the chart shows enough bars.
  let labels: string[] = [];
  await expect(async () => {
    labels = [];
    for (let x = box.width * 0.4; x < box.width * 0.8; x += 3) {
      await page.mouse.move(box.x + x, box.y + box.height * 0.4);
      const label = (await time.textContent()) ?? '';
      if (label && labels.at(-1) !== label) labels.push(label);
    }
    expect(labels.length).toBeGreaterThan(5);
  }).toPass({timeout: 30_000});
  for (const label of labels) {
    expect(label).toMatch(/(09|10|11|12|13|14|15):30 ET$/);
  }
  await expect(page.getByTestId('data-window')).toContainText(
    /Delta %\s*[−+-]?\d/
  );
  await page.screenshot({path: 'e2e/screenshots/41-fullstack-sessions.png'});
});

/**
 * Runs the anchored VWAP built-in on the DEMO perpetual's 1m bars.
 * @param request Playwright request context.
 * @param barsBack Bars between the anchor and the latest bar.
 * @return The study's VWAP values and the table texts it drew.
 */
async function anchoredVwap(
  request: APIRequestContext,
  barsBack: number
): Promise<{values: Array<number | null>; tables: string}> {
  const latest = await request.get(`${MARKET}/v1/candles`, {
    params: {instrument_id: 'mock:perp:BTCUSD', tf: '1m', limit: 1},
  });
  const [bar] = ((await latest.json()) as {candles: Candle[]}).candles;
  if (!bar) throw new Error('no bar');
  const anchor = bar.start_ms - barsBack * 60_000;
  const response = await request.post(`${MARKET}/v1/scripts/run-batch`, {
    headers: {Authorization: `Bearer ${TOKEN}`},
    data: {
      instrument_id: 'mock:perp:BTCUSD',
      tf: '1m',
      from_ms: anchor,
      limit: 5000,
      runs: [
        {
          script_id: 'builtin:anchored_vwap',
          inputs: {anchor: 'time', anchor_ms: anchor, source: 'hlc3'},
        },
      ],
    },
  });
  expect(response.ok()).toBeTruthy();
  const body = (await response.json()) as {
    results: Array<{
      result: {
        plots: Array<{name: string; points: Array<{value: number | null}>}>;
        visuals: Array<{kind: string; data: Record<string, unknown>}>;
      };
    }>;
  };
  const result = body.results[0]?.result;
  return {
    values:
      result?.plots
        .find(plot => plot.name === 'VWAP')
        ?.points.map(point => point.value) ?? [],
    tables: JSON.stringify(
      result?.visuals.filter(visual => visual.kind === 'table') ?? []
    ),
  };
}

test('engine anchored VWAP accepts anchors beyond 2000 bars', async ({
  request,
}) => {
  const {values} = await anchoredVwap(request, 3000);
  expect(values.some(value => typeof value === 'number')).toBeTruthy();
});

test('the engine reports anchors beyond the loaded bars', async ({request}) => {
  const {values, tables} = await anchoredVwap(request, 6000);
  expect(values.every(value => value === null)).toBeTruthy();
  expect(tables).toContain('Anchor is outside the loaded bars');
});
