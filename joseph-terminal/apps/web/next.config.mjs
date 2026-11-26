/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Next.js configuration: transpiles workspace packages and
 * sets security headers, including a Content Security Policy (CSP) that
 * allows `eval` only in development.
 */

import path from 'node:path';
import {fileURLToPath} from 'node:url';

/**
 * Collects the origins (HTTP and WebSocket) of the configured engines, which
 * the browser may connect to.
 * @return {string} Space-separated CSP source list; empty in DEMO mode.
 */
function connectSources() {
  const urls = [
    process.env.NEXT_PUBLIC_MARKET_API_URL,
    process.env.NEXT_PUBLIC_MARKET_WS_URL,
    process.env.NEXT_PUBLIC_EXECUTION_API_URL,
    process.env.NEXT_PUBLIC_EXECUTION_WS_URL,
  ].filter(Boolean);
  const origins = new Set();
  for (const url of urls) {
    try {
      origins.add(new URL(url).origin);
    } catch {
      // Ignore malformed values; the app reports misconfiguration in the UI.
    }
  }
  return [...origins].join(' ');
}

/** Whether this is a development build (`next dev`). */
const isDev = process.env.NODE_ENV !== 'production';

/**
 * Content Security Policy header value. Inline scripts are allowed for the
 * theme boot script in the root layout and the scripts Next.js inlines;
 * inline styles for React `style` attributes. Development builds also need
 * `eval` (webpack's development bundles) and WebSocket connections for hot
 * reloading.
 */
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ''}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${connectSources()}${isDev ? ' ws: wss:' : ''}`.trim(),
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
  "worker-src 'self'",
].join('; ');

/** Monorepo root, used for output file tracing. */
const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '../..');

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Container images set NEXT_OUTPUT=standalone for a minimal Node server.
  output: process.env.NEXT_OUTPUT === 'standalone' ? 'standalone' : undefined,
  outputFileTracingRoot: ROOT,
  transpilePackages: [
    '@joseph/contracts',
    '@joseph/feature-registry',
    '@joseph/chart-core',
    '@joseph/drawing-core',
    '@joseph/drawing-tools',
    '@joseph/indicator-viz',
    '@joseph/terminal-shell',
    '@joseph/chart-trading',
    '@joseph/strategy-builder',
    '@joseph/mock-provider',
  ],
  distDir: process.env.NEXT_DIST_DIR || '.next',
  async headers() {
    return [
      {
        source: '/alerts-sw.js',
        headers: [
          {key: 'Cache-Control', value: 'no-cache'},
          {key: 'Service-Worker-Allowed', value: '/'},
        ],
      },
      {
        source: '/:path*',
        headers: [
          {key: 'Content-Security-Policy', value: csp},
          {key: 'X-Content-Type-Options', value: 'nosniff'},
          {key: 'Referrer-Policy', value: 'no-referrer'},
          {key: 'X-Frame-Options', value: 'DENY'},
          {
            key: 'Permissions-Policy',
            value: 'camera=(), microphone=(), geolocation=(), payment=()',
          },
        ],
      },
    ];
  },
};

export default nextConfig;
