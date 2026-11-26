/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Executes the production worker to verify background alerts. */

import {readFileSync} from 'node:fs';
import {createContext, runInContext} from 'node:vm';
import {describe, expect, it, vi} from 'vitest';

/**
 * Runs the actual worker with controlled browser lifecycle dependencies.
 * @return Registered event listeners and observable notification/window APIs.
 */
function worker() {
  const listeners = new Map<string, (event: unknown) => void>();
  const showNotification = vi.fn().mockResolvedValue(undefined);
  const focus = vi.fn().mockResolvedValue(undefined);
  const matchAll = vi
    .fn()
    .mockResolvedValue([{url: 'https://terminal.example/', focus}]);
  const openWindow = vi.fn().mockResolvedValue(undefined);
  const scope = {
    addEventListener: (name: string, listener: (event: unknown) => void) =>
      listeners.set(name, listener),
    registration: {showNotification},
    clients: {matchAll, openWindow},
    location: {origin: 'https://terminal.example'},
  };
  runInContext(
    readFileSync('apps/web/public/alerts-sw.js', 'utf8'),
    createContext({self: scope, URL})
  );
  return {listeners, showNotification, focus, matchAll, openWindow};
}

describe('alert service worker', () => {
  it('displays background push with bounded text and a stable deduplication tag', async () => {
    const api = worker();
    const waitUntil = vi.fn();
    api.listeners.get('push')?.({
      data: {
        json: () => ({
          title: 'Joseph alert',
          body: 'price signal',
          tag: 'delivery-1',
          url: 'https://evil.example',
        }),
      },
      waitUntil,
    });
    await waitUntil.mock.calls[0]?.[0];
    expect(api.showNotification).toHaveBeenCalledWith(
      'Joseph alert',
      expect.objectContaining({
        body: 'price signal',
        tag: 'delivery-1',
        data: {url: '/'},
      })
    );
  });

  it('shows a fallback for malformed push data', async () => {
    const api = worker();
    const waitUntil = vi.fn();
    api.listeners.get('push')?.({
      data: {
        json: () => {
          throw new Error('invalid JSON');
        },
      },
      waitUntil,
    });
    await waitUntil.mock.calls[0]?.[0];
    expect(api.showNotification).toHaveBeenCalledWith(
      'Joseph alert',
      expect.objectContaining({body: 'Open Joseph to view the alert.'})
    );
  });

  it('focuses an existing window and never opens a payload-controlled URL', async () => {
    const api = worker();
    const waitUntil = vi.fn();
    const close = vi.fn();
    api.listeners.get('notificationclick')?.({
      notification: {close, data: {url: 'https://evil.example'}},
      waitUntil,
    });
    await waitUntil.mock.calls[0]?.[0];
    expect(close).toHaveBeenCalledOnce();
    expect(api.focus).toHaveBeenCalledOnce();
    expect(api.openWindow).not.toHaveBeenCalled();
    api.matchAll.mockResolvedValue([]);
    api.listeners.get('notificationclick')?.({
      notification: {close},
      waitUntil,
    });
    await waitUntil.mock.calls[1]?.[0];
    expect(api.openWindow).toHaveBeenCalledWith('/');
  });
});
