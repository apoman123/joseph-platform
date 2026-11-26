/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Root layout: fonts, global stylesheets, page metadata and
 * the inline script that applies the saved theme before first paint.
 */

import type {Metadata, Viewport} from 'next';
import {Archivo} from 'next/font/google';
import type {ReactNode, ReactElement} from 'react';
import '../styles/tokens.css';
import '../styles/components.css';
import '../styles/shell.css';
import '../styles/panes.css';

/** Archivo variable font with the width axis (semi-condensed numerals). */
const archivo = Archivo({
  subsets: ['latin'],
  axes: ['wdth'],
  variable: '--font-archivo',
  display: 'swap',
});

/** Document title and description. */
export const metadata: Metadata = {
  title: 'Joseph Terminal',
  description: 'Open-source multi-venue perpetuals charting terminal.',
  manifest: '/manifest.webmanifest',
  icons: {icon: '/icon.svg', apple: '/icon-192.png'},
  appleWebApp: {
    capable: true,
    title: 'Joseph',
    statusBarStyle: 'black-translucent',
  },
};

/** Viewport and browser theme colour (matches the dark background). */
export const viewport: Viewport = {
  themeColor: '#0c1820',
  width: 'device-width',
  initialScale: 1,
};

/**
 * Inline script that applies the saved light theme before first paint, so a
 * light-theme user never sees a dark flash. It must stay a string: it runs
 * before React hydrates. Storage access can throw (private mode, blocked
 * storage), in which case the dark default stays.
 */
const THEME_BOOT = `try{var t=localStorage.getItem('joseph.theme');if(t==='light'){document.documentElement.dataset.theme='light'}}catch(e){}`;

/**
 * Renders the HTML document around every page. The `<html>` element starts
 * dark and `suppressHydrationWarning` tolerates the boot script switching
 * `data-theme` before React hydrates.
 * @param props Page content.
 * @return Document.
 */
export default function RootLayout({
  children,
}: {
  children: ReactNode;
}): ReactElement {
  return (
    <html
      lang="en"
      data-theme="dark"
      className={archivo.variable}
      suppressHydrationWarning
    >
      <head>
        {/* eslint-disable-next-line no-restricted-syntax -- THEME_BOOT is a constant, not user input, and must run before hydration to avoid a theme flash. */}
        <script dangerouslySetInnerHTML={{__html: THEME_BOOT}} />
      </head>
      <body>{children}</body>
    </html>
  );
}
