/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Explicit device registration for standard encrypted Web Push. */

import type {AlertSubscription, StrategyClient} from '@joseph/contracts';

/**
 * Converts the public VAPID application server key to browser bytes.
 * @param text Unpadded base64url key.
 * @return Fresh byte array for PushManager.subscribe.
 */
export function decodePushKey(text: string): Uint8Array<ArrayBuffer> {
  const raw = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, char => char.charCodeAt(0));
}

/**
 * Derives a stable public device id without exposing its endpoint in the UI.
 * @param endpoint Private browser push endpoint.
 * @return SHA-256 device identifier matching server validation.
 */
export async function pushDeviceId(endpoint: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(endpoint)
  );
  return `device-${Array.from(new Uint8Array(digest), byte =>
    byte.toString(16).padStart(2, '0')
  )
    .join('')
    .slice(0, 32)}`;
}

/**
 * Registers this device after a direct user gesture and persists its filters.
 * @param client Authenticated strategy client; private keys are write only.
 * @param publicKey Server's public VAPID key.
 * @return Saved destination without private browser registration material.
 * @throws Error If unsupported, denied, misconfigured, or registration fails.
 */
export async function enableDevicePush(
  client: StrategyClient,
  publicKey: string
): Promise<AlertSubscription> {
  if (
    !window.isSecureContext ||
    !('serviceWorker' in navigator) ||
    !('PushManager' in window) ||
    !('Notification' in window)
  ) {
    throw new Error(
      'Push requires HTTPS and a supported browser. On iPhone or iPad, add Joseph to the Home Screen and open it there.'
    );
  }
  if (!publicKey) throw new Error('The server has no Web Push configuration.');
  // Request before the first await to preserve the user activation on Safari.
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    throw new Error(
      'Notifications are blocked. Allow notifications in your browser settings, then try again.'
    );
  }
  const registration = await navigator.serviceWorker.register('/alerts-sw.js', {
    scope: '/',
  });
  await navigator.serviceWorker.ready;
  const existing = await registration.pushManager.getSubscription();
  const subscription =
    existing ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: decodePushKey(publicKey),
    }));
  try {
    const deviceId = await pushDeviceId(subscription.endpoint);
    const json = subscription.toJSON();
    if (!json.keys?.p256dh || !json.keys.auth) {
      throw new Error('The browser did not provide push encryption keys.');
    }
    // Re-enabling a device preserves its selected strategies and quiet hours.
    const list = await client.listAlertSubscriptions();
    const old = list.subscriptions.find(
      row => row.channel === 'WEB_PUSH' && row.target === deviceId
    );
    return await client.putAlertSubscription({
      subscription_id: deviceId,
      channel: 'WEB_PUSH',
      target: deviceId,
      kinds: [],
      min_severity: 'INFO',
      strategy_ids: [],
      quiet_hours: null,
      ...old,
      enabled: true,
      web_push: {
        endpoint: subscription.endpoint,
        keys: {p256dh: json.keys.p256dh, auth: json.keys.auth},
      },
    });
  } catch (error) {
    if (!existing) await subscription.unsubscribe().catch(() => false);
    throw error;
  }
}

/**
 * Unsubscribes locally only when a removed destination matches this device.
 * @param target Public device identifier of the deleted server destination.
 * @return Completion; other devices and destinations remain registered.
 */
export async function removeDevicePush(target: string): Promise<void> {
  if (!('serviceWorker' in navigator)) return;
  const registration = await navigator.serviceWorker.getRegistration('/');
  const subscription = await registration?.pushManager.getSubscription();
  if (subscription && (await pushDeviceId(subscription.endpoint)) === target) {
    await subscription.unsubscribe();
  }
}
