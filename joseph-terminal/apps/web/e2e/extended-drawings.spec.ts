/**
 * @fileoverview System tests (DEMO) of the extended drawing tier and the
 * engine-backed tools: P3 shapes and patterns, anchored VWAP and the
 * regression channel. Canvas text is not in the DOM, so results are read
 * from the object list and the data window.
 */
import {expect, test} from '@playwright/test';
import {openPanel} from './trading-helpers';
import {
  armTool,
  BTC_DRAWINGS_KEY,
  clickChart,
  dragOnChart,
  hoverChart,
  objectDetail,
  openTerminal,
  savedTools,
  showDataWindow,
} from './support';

const SHOTS = 'e2e/screenshots';

test('draws P3 shapes and an XABCD pattern with ratio labels', async ({
  page,
}) => {
  await openTerminal(page);
  await armTool(page, 'shapes', 'ellipse');
  await dragOnChart(page, [0.3, 0.3], [0.42, 0.5]);
  await expect.poll(() => savedTools(page)).toEqual(['ellipse']);

  await armTool(page, 'patterns', 'xabcd-pattern');
  for (const [x, y] of [
    [0.2, 0.62],
    [0.3, 0.3],
    [0.4, 0.5],
    [0.5, 0.26],
    [0.6, 0.56],
  ] as const) {
    await clickChart(page, x, y);
  }
  await expect
    .poll(() => savedTools(page))
    .toEqual(['ellipse', 'xabcd-pattern']);

  await openPanel(page, 'Objects');
  await expect(objectDetail(page, 'xabcd-pattern')).toHaveText(
    /^AB\/XA \d\.\d{3}, BC\/AB \d\.\d{3}, CD\/BC \d\.\d{3}, AD\/XA \d\.\d{3}$/
  );
  await expect(
    page.getByTestId('object-ellipse').getByText('Ellipse', {exact: true})
  ).toBeVisible();
  await page.screenshot({path: `${SHOTS}/25-extended-drawings.png`});

  // Lock prevents deletion; undo removes the pattern again.
  const row = page.getByTestId('object-xabcd-pattern');
  await row.getByRole('button', {name: 'Lock XABCD pattern'}).click();
  await expect(
    row.getByRole('button', {name: 'Delete XABCD pattern'})
  ).toBeDisabled();
  await row.getByRole('button', {name: 'Unlock XABCD pattern'}).click();
  await page.keyboard.press('Control+z');
  await page.keyboard.press('Control+z');
  await page.keyboard.press('Control+z');
  await expect.poll(() => savedTools(page)).toEqual(['ellipse']);
});

test('draws an anchored VWAP and a regression channel from engine series', async ({
  page,
}) => {
  await openTerminal(page);
  await showDataWindow(page);

  await armTool(page, 'studies', 'anchored-vwap');
  await clickChart(page, 0.5, 0.5);
  await expect.poll(() => savedTools(page)).toEqual(['anchored-vwap']);
  await openPanel(page, 'Objects');
  await expect(objectDetail(page, 'anchored-vwap')).toHaveText(
    /^VWAP [\d,]+\.\d\d from \d{4}-\d\d-\d\d \d\d:\d\d UTC · bands 1σ$/
  );
  await hoverChart(page, 0.68, 0.4);
  const data = page.getByTestId('data-window');
  await expect(data).toContainText(/Anchored VWAP\s*[\d,]+\.\d/);
  await expect(data).toContainText(/VWAP \+1σ\s*[\d,]+\.\d/);

  // Band 2 from the style bar re-requests the engine series.
  const style = page.getByTestId('drawing-style-bar');
  await style.getByLabel('Band 2').selectOption('2');
  await expect(objectDetail(page, 'anchored-vwap')).toContainText(
    'bands 1σ, 2σ'
  );
  await hoverChart(page, 0.7, 0.42);
  await expect(data).toContainText(/VWAP \+2σ\s*[\d,]+\.\d/);

  await armTool(page, 'studies', 'regression-channel');
  await dragOnChart(page, [0.3, 0.5], [0.62, 0.45]);
  await expect
    .poll(() => savedTools(page))
    .toEqual(['anchored-vwap', 'regression-channel']);
  await expect(objectDetail(page, 'regression-channel')).toHaveText(
    /^R -?\d\.\d{3} · end [\d,]+\.\d\d · \d+ bars$/
  );
  await hoverChart(page, 0.66, 0.4);
  await expect(data).toContainText(/Pearson's R\s*-?\d\.\d{3}/);
  await expect(data).toContainText(/Channel upper\s*[\d,]+\.\d/);
  await page.screenshot({path: `${SHOTS}/26-engine-drawings.png`});

  // Survives a reload: drawings and their engine values come back.
  await page.reload();
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  await openPanel(page, 'Objects');
  await expect(objectDetail(page, 'regression-channel')).toHaveText(/^R /);
});

test('explains an anchored VWAP more than 2000 bars back', async ({page}) => {
  await openTerminal(page);
  await page.evaluate(key => {
    const bar = 300_000;
    const anchor = Math.floor(Date.now() / bar) * bar - 2400 * bar;
    localStorage.setItem(
      key,
      JSON.stringify({
        schema_version: 1,
        workspace_id: 'default',
        instrument_id: 'mock:perp:BTCUSD',
        drawings: [
          {
            schema_version: 1,
            drawing_id: 'far-vwap',
            tool_id: 'anchored-vwap',
            tool_version: 1,
            instrument_id: 'mock:perp:BTCUSD',
            timeframe: null,
            anchors: [{time_ms: anchor, price: '95000'}],
            style: {color: '#e66fd2', line_width: 2, line_style: 'solid'},
            text: null,
            props: {band_1: '1', band_2: '0', band_3: '0'},
            locked: false,
            visible: true,
            z_index: 1,
            created_at_ms: anchor,
            updated_at_ms: anchor,
          },
        ],
      })
    );
  }, BTC_DRAWINGS_KEY);
  await page.reload();
  await expect(page.locator('.terminal[data-ready="true"]')).toBeVisible();
  await openPanel(page, 'Objects');
  await expect(objectDetail(page, 'anchored-vwap')).toHaveText(
    'Anchor is more than 2000 bars back — move it closer'
  );
  await showDataWindow(page);
  await hoverChart(page, 0.6, 0.4);
  await expect(page.getByTestId('data-window')).toContainText(
    'anchor > 2000 bars back'
  );
  await page.screenshot({path: `${SHOTS}/27-vwap-anchor-too-far.png`});
});
