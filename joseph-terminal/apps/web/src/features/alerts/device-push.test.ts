/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Permission, registration rollback and device removal tests. */

import {DemoStrategyClient} from '@joseph/mock-provider';
import {webcrypto} from 'node:crypto';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {enableDevicePush, pushDeviceId, removeDevicePush} from './device-push';

/**
 * Installs a controlled browser push API without contacting any push service.
 * @param permission Permission returned by the explicit request.
 * @return Push API spies and registration data.
 */
function installPush(permission: NotificationPermission = 'granted') {
  const unsubscribe = vi.fn().mockResolvedValue(true);
  const subscription = {
    endpoint: 'https://fcm.googleapis.com/fcm/send/test-device',
    unsubscribe,
    toJSON: () => ({keys: {p256dh: 'public-encryption-key', auth: 'auth-key'}}),
  };
  const getSubscription = vi.fn().mockResolvedValue(null);
  const subscribe = vi.fn().mockResolvedValue(subscription);
  const registration = {pushManager: {getSubscription, subscribe}};
  const register = vi.fn().mockResolvedValue(registration);
  const requestPermission = vi.fn().mockResolvedValue(permission);
  vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('isSecureContext', true);
  vi.stubGlobal('PushManager', {});
  vi.stubGlobal('Notification', {requestPermission});
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: {
      register,
      ready: Promise.resolve(registration),
      getRegistration: vi.fn().mockResolvedValue(registration),
    },
  });
  return {
    unsubscribe,
    subscription,
    getSubscription,
    subscribe,
    register,
    requestPermission,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, 'serviceWorker');
});

describe('device push', () => {
  it('requests permission only on explicit enable, then saves write-only device data', async () => {
    const api = installPush();
    const client = new DemoStrategyClient();
    expect(api.requestPermission).not.toHaveBeenCalled();
    const saved = await enableDevicePush(client, 'BAAB');
    expect(api.register).toHaveBeenCalledWith('/alerts-sw.js', {scope: '/'});
    expect(saved.channel).toBe('WEB_PUSH');
    expect(saved.target).toMatch(/^device-[a-f0-9]{32}$/);
    expect(saved.web_push).toBeUndefined();
    expect(api.unsubscribe).not.toHaveBeenCalled();
  });

  it('does not register a worker after permission is denied', async () => {
    const api = installPush('denied');
    await expect(
      enableDevicePush(new DemoStrategyClient(), 'BAAB')
    ).rejects.toThrow('blocked');
    expect(api.register).not.toHaveBeenCalled();
  });

  it('reports insecure or unsupported contexts without prompting', async () => {
    const api = installPush();
    vi.stubGlobal('isSecureContext', false);
    await expect(
      enableDevicePush(new DemoStrategyClient(), 'BAAB')
    ).rejects.toThrow('HTTPS');
    expect(api.requestPermission).not.toHaveBeenCalled();
  });

  it('rolls back a new local subscription when server persistence fails', async () => {
    const api = installPush();
    const client = new DemoStrategyClient();
    vi.spyOn(client, 'putAlertSubscription').mockRejectedValue(
      new Error('offline')
    );
    await expect(enableDevicePush(client, 'BAAB')).rejects.toThrow('offline');
    expect(api.unsubscribe).toHaveBeenCalledOnce();
  });

  it('reuses the current endpoint and preserves filters and quiet hours', async () => {
    const api = installPush();
    const client = new DemoStrategyClient();
    const saved = await enableDevicePush(client, 'BAAB');
    await client.putAlertSubscription({
      ...saved,
      min_severity: 'CRITICAL',
      strategy_ids: ['my-strategy'],
      quiet_hours: {
        start: '22:00',
        end: '07:00',
        tz: 'UTC',
        bypass_severities: ['CRITICAL'],
      },
    });
    api.getSubscription.mockResolvedValue(api.subscription);
    const updated = await enableDevicePush(client, 'BAAB');
    expect(updated.min_severity).toBe('CRITICAL');
    expect(updated.strategy_ids).toEqual(['my-strategy']);
    expect(updated.quiet_hours?.start).toBe('22:00');
    expect(api.subscribe).toHaveBeenCalledOnce();
  });

  it('unsubscribes only when the removed target belongs to this device', async () => {
    const api = installPush();
    api.getSubscription.mockResolvedValue(api.subscription);
    await removeDevicePush('another-device');
    expect(api.unsubscribe).not.toHaveBeenCalled();
    await removeDevicePush(await pushDeviceId(api.subscription.endpoint));
    expect(api.unsubscribe).toHaveBeenCalledOnce();
  });
});
