/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Production browser → API tests of private push registration. */

import {expect, test} from '@playwright/test';
import {generateKeyPairSync, randomBytes} from 'node:crypto';
import {API, TOKEN} from '../playwright.fullstack.config';
import {loadTerminal, openPanel} from '../e2e/trading-helpers';

/** Authorization header for the isolated system-test tenant. */
const AUTH = {Authorization: `Bearer ${TOKEN}`};

test('registers a real service worker, saves private push input and deletes a device', async ({
  page,
  request,
}) => {
  const {publicKey} = generateKeyPairSync('ec', {namedCurve: 'prime256v1'});
  const jwk = publicKey.export({format: 'jwk'});
  const p256dh = Buffer.concat([
    Buffer.from([4]),
    Buffer.from(jwk.x ?? '', 'base64url'),
    Buffer.from(jwk.y ?? '', 'base64url'),
  ]).toString('base64url');
  // Mock only the external push service subscription. The production service
  // worker, browser crypto, UI, HTTP clients and Python storage all run normally.
  await page.addInitScript(
    ({key, auth}) => {
      Notification.requestPermission = () => Promise.resolve('granted');
      // eslint-disable-next-line no-restricted-syntax -- Replace the native push-service boundary without contacting a real device.
      PushManager.prototype.getSubscription = () => Promise.resolve(null);
      // eslint-disable-next-line no-restricted-syntax -- Replace the native push-service boundary without contacting a real device.
      PushManager.prototype.subscribe = () =>
        Promise.resolve({
          endpoint: 'https://fcm.googleapis.com/fcm/send/fullstack-device',
          toJSON: () => ({keys: {p256dh: key, auth}}),
          unsubscribe: () => Promise.resolve(true),
        } as unknown as PushSubscription);
    },
    {key: p256dh, auth: randomBytes(16).toString('base64url')}
  );
  await loadTerminal(page);
  await openPanel(page, 'Notifications');
  const panel = page.getByTestId('notifications-panel');
  await panel.getByRole('button', {name: 'Enable push on this device'}).click();
  await expect(
    panel.getByText('Push enabled on this device.', {exact: false})
  ).toBeVisible();
  const registration = await page.evaluate(async () => {
    const worker = await navigator.serviceWorker.getRegistration('/');
    return worker?.active?.scriptURL;
  });
  expect(registration).toContain('/alerts-sw.js');
  const response = await request.get(
    `${API.NEXT_PUBLIC_MARKET_API_URL}/v1/alerts/subscriptions`,
    {headers: AUTH}
  );
  const listing = await response.json();
  const saved = listing.subscriptions.find(
    (row: {channel: string}) => row.channel === 'WEB_PUSH'
  );
  expect(saved).toBeTruthy();
  expect(saved.web_push).toBeUndefined();
  expect(JSON.stringify(listing)).not.toContain('fullstack-device');
  await panel.getByLabel(`Delete ${saved.subscription_id}`).click();
  await expect(
    panel.getByRole('row', {name: new RegExp(saved.subscription_id)})
  ).toHaveCount(0);
});

test('reports denied push permission without creating a destination', async ({
  page,
}) => {
  await page.addInitScript(() => {
    Notification.requestPermission = () => Promise.resolve('denied');
  });
  await loadTerminal(page);
  await openPanel(page, 'Notifications');
  const panel = page.getByTestId('notifications-panel');
  await panel.getByRole('button', {name: 'Enable push on this device'}).click();
  await expect(panel.getByRole('alert')).toContainText(
    'Notifications are blocked'
  );
});

test('saves WhatsApp targets and rejects an unconfigured test through the API', async ({
  page,
  request,
}) => {
  await loadTerminal(page);
  await openPanel(page, 'Notifications');
  const panel = page.getByTestId('notifications-panel');
  await panel.getByLabel('Name', {exact: true}).fill('whatsapp-system');
  await panel
    .getByRole('combobox', {name: 'Channel', exact: true})
    .selectOption('WHATSAPP');
  await panel.getByLabel('Phone number', {exact: true}).fill('+14155552671');
  await panel.getByRole('button', {name: 'Save notification'}).click();
  const row = panel.getByRole('row', {name: /whatsapp-system/});
  await expect(row).toContainText('WhatsApp');
  await expect(row.getByRole('button', {name: /Send test/})).toBeDisabled();
  const response = await request.post(
    `${API.NEXT_PUBLIC_MARKET_API_URL}/v1/alerts/subscriptions/whatsapp-system/test`,
    {headers: AUTH}
  );
  expect(response.ok()).toBe(false);
  expect((await response.json()).code).toBe('UNSUPPORTED');
  await row.getByRole('button', {name: /Delete/}).click();
  await expect(row).toHaveCount(0);
});
