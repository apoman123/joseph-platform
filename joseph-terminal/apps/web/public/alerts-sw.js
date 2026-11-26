/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Displays background alerts; it never caches market data. */

/* global self -- Browser service worker global scope. */

self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', event => {
  let data;
  try {
    data = event.data?.json() ?? {};
  } catch {
    data = {};
  }
  event.waitUntil(
    self.registration.showNotification(String(data.title ?? 'Joseph alert'), {
      body: String(data.body ?? 'Open Joseph to view the alert.').slice(
        0,
        1500
      ),
      tag: String(data.tag ?? 'joseph-alert'),
      icon: '/icon.svg',
      data: {url: '/'},
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({
        type: 'window',
        includeUncontrolled: true,
      });
      const client = windows.find(
        item => new URL(item.url).origin === self.location.origin
      );
      if (client) return client.focus();
      return self.clients.openWindow('/');
    })()
  );
});
