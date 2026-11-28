/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Real Python studies, heatmap tabs and automatic MCP control. */

import {expect, test} from '@playwright/test';
import {API, TOKEN} from '../playwright.fullstack.config';

/** Ordinary test-user authentication; never an external notification recipient. */
const AUTH = {Authorization: `Bearer ${TOKEN}`};
/** Market-engine origin the web build talks to. */
const MARKET = API.NEXT_PUBLIC_MARKET_API_URL;

test('new heatmap tab loads the engine catalogue and snapshot honestly', async ({
  page,
  request,
}) => {
  const api = await request.get(`${MARKET}/v1/heatmap`, {
    headers: AUTH,
    params: {type: 'crypto'},
  });
  expect(api.status()).toBe(200);
  const body = (await api.json()) as {
    type: string;
    tiles: unknown[];
    size_metrics: Array<{id: string}>;
  };
  expect(body.type).toBe('crypto');
  expect(Array.isArray(body.tiles)).toBe(true);
  expect(body.size_metrics.map(row => row.id)).toContain('market_cap');
  await page.goto('/');
  await page.getByRole('button', {name: 'Add chart tab'}).click();
  await page.getByRole('button', {name: 'Heatmap', exact: true}).click();
  const heatmap = page.getByRole('region', {name: 'Heatmap'});
  await expect(heatmap.getByLabel('Data set')).toBeVisible();
  await expect(heatmap.getByRole('option', {name: /^S&P 500/})).toHaveCount(1);
  // Either real tiles or an explicit empty/unavailable state, never a hang.
  await expect(
    heatmap
      .getByRole('group', {name: /tiles$/})
      .getByRole('button', {name: /,/})
      .first()
      .or(
        heatmap
          .getByRole('status')
          .filter({hasText: /No symbols?|unavailable|error/i})
          .first()
      )
  ).toBeVisible({timeout: 30_000});
  await page.reload();
  await expect(
    page.getByRole('tab', {name: 'Heatmap', exact: true})
  ).toHaveAttribute('aria-selected', 'true');
});

test('Python editor saves, executes and attaches a real isolated indicator', async ({
  page,
}) => {
  test.skip(
    process.env.JOSEPH_SCRIPT_TESTS !== '1',
    'Build the isolated runner and enable JOSEPH_SCRIPT_TESTS=1'
  );
  await page.goto('/');
  await page.getByTestId('instrument-selector').click();
  await page.getByRole('textbox', {name: 'Search instruments'}).fill('BTCUSD');
  await page.getByTestId('instrument-option-mock:perp:BTCUSD').click();
  await page.getByRole('tab', {name: 'Python editor', exact: true}).click();
  const editor = page.getByRole('region', {name: 'Python scripts'});
  await editor
    .getByRole('button', {name: 'New indicator', exact: true})
    .click();
  await editor
    .getByLabel('Script ID', {exact: true})
    .fill(`browser-study-${Date.now()}`);
  await editor.getByLabel('Name', {exact: true}).fill('Browser Python average');
  await editor.getByRole('button', {name: 'Save version', exact: true}).click();
  await expect(editor).toContainText('Saved immutable v1.');
  await editor
    .getByRole('button', {name: 'Preview on closed bars', exact: true})
    .click();
  await expect(
    editor.getByRole('region', {name: 'Python execution report'})
  ).toContainText('DEMO');
  await editor.getByRole('button', {name: 'Add to chart', exact: true}).click();
  const legend = page.getByLabel('Active indicators');
  await expect(legend).toContainText('Browser Python average · v1');
  await legend
    .getByRole('button', {name: 'Hide Browser Python average'})
    .click();
  await expect(
    legend.getByRole('button', {name: 'Show Browser Python average'})
  ).toBeVisible();
  await legend
    .getByRole('button', {name: 'Show Browser Python average'})
    .click();
  await legend
    .getByRole('button', {name: 'Browser Python average settings'})
    .click();
  const settings = page.getByRole('dialog', {
    name: 'Browser Python average settings',
  });
  await settings.getByLabel('Inputs (JSON object)').fill('{"length":12}');
  await settings.getByRole('button', {name: 'Apply settings'}).click();
  await legend
    .getByRole('button', {name: 'Browser Python average source'})
    .click();
  await expect(editor.getByLabel('Python source')).toHaveValue(
    /class MyIndicator\(Indicator\)/
  );
  await legend
    .getByRole('button', {name: 'Remove Browser Python average'})
    .click();
  await expect(legend).not.toContainText('Browser Python average');
});

test('MCP starts with the backend and controls an explicitly enabled browser across panels', async ({
  page,
  request,
}) => {
  await page.goto('/');
  await page.getByRole('tab', {name: 'MCP', exact: true}).click();
  const panel = page.getByRole('region', {name: 'MCP settings'});
  await panel.getByLabel('Allow writes', {exact: true}).check();
  await panel.getByRole('button', {name: 'Create MCP connection'}).click();
  const token = await panel
    .getByRole('textbox', {name: /One-time MCP token/})
    .inputValue();
  await panel.getByLabel('Enable MCP browser control for this window').check();
  await expect(panel).toContainText('Browser control connected');
  const headers = {
    'Authorization': `Bearer ${token}`,
    'Accept': 'application/json, text/event-stream',
    'Mcp-Protocol-Version': '2026-07-28',
  };
  const meta = {
    'io.modelcontextprotocol/protocolVersion': '2026-07-28',
    'io.modelcontextprotocol/clientCapabilities': {},
  };
  const discovery = await request.post(
    `${API.NEXT_PUBLIC_MARKET_API_URL}/mcp`,
    {
      headers: {...headers, 'Mcp-Method': 'tools/list'},
      data: {
        jsonrpc: '2.0',
        id: 1,
        method: 'tools/list',
        params: {_meta: meta},
      },
    }
  );
  expect(discovery.status()).toBe(200);
  const tools = (await discovery.json()).result.tools as Array<{name: string}>;
  expect(tools.some(tool => tool.name === 'market_get_scripts')).toBe(true);
  expect(tools.some(tool => tool.name.includes('connections'))).toBe(false);
  const sessions = await request.get(
    `${API.NEXT_PUBLIC_MARKET_API_URL}/v1/terminal/sessions`,
    {headers: AUTH}
  );
  const sessionId = (await sessions.json()).sessions[0]?.session_id as
    string | undefined;
  expect(sessionId).toBeTruthy();
  const actions = tools.find(
    tool =>
      tool.name.startsWith('market_post_terminal_sessions_') &&
      tool.name.endsWith('_actions')
  );
  expect(actions).toBeDefined();
  const openEditor = await request.post(
    `${API.NEXT_PUBLIC_MARKET_API_URL}/mcp`,
    {
      headers: {
        ...headers,
        'Mcp-Method': 'tools/call',
        'Mcp-Name': actions?.name ?? '',
      },
      data: {
        jsonrpc: '2.0',
        id: 2,
        method: 'tools/call',
        params: {
          _meta: meta,
          name: actions?.name,
          arguments: {
            path: {session_id: sessionId},
            body: {
              operation: 'command',
              command_id: 'shell.showPanel',
              arguments: {slot: 'bottom-panel', id: 'shell.python'},
            },
          },
        },
      },
    }
  );
  expect((await openEditor.json()).result.isError).not.toBe(true);
  await expect(
    page.getByRole('region', {name: 'Python scripts'})
  ).toBeVisible();
  const theme = await request.post(`${API.NEXT_PUBLIC_MARKET_API_URL}/mcp`, {
    headers: {
      ...headers,
      'Mcp-Method': 'tools/call',
      'Mcp-Name': actions?.name ?? '',
    },
    data: {
      jsonrpc: '2.0',
      id: 3,
      method: 'tools/call',
      params: {
        _meta: meta,
        name: actions?.name,
        arguments: {
          path: {session_id: sessionId},
          body: {operation: 'theme', arguments: {theme: 'light'}},
        },
      },
    },
  });
  expect((await theme.json()).result.isError).not.toBe(true);
  await expect(page.locator('[data-theme="light"]').first()).toBeVisible();
  const metadata = await request.get(
    `${API.NEXT_PUBLIC_MARKET_API_URL}/.well-known/oauth-protected-resource/mcp`
  );
  expect(metadata.status()).toBe(200);
  expect((await metadata.json()).authorization_servers).toBeUndefined();
});
