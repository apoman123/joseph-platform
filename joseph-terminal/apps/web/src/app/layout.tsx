/**
 * @fileoverview Root layout: fonts, design tokens and theme bootstrap.
 */
import type {Metadata, Viewport} from 'next';
import {Archivo} from 'next/font/google';
import type {ReactNode, ReactElement} from 'react';
import '../styles/tokens.css';
import '../styles/components.css';
import '../styles/shell.css';

/** Archivo variable font with the width axis (semi-condensed numerals). */
const archivo = Archivo({
  subsets: ['latin'],
  axes: ['wdth'],
  variable: '--font-archivo',
  display: 'swap',
});

/** Page metadata. */
export const metadata: Metadata = {
  title: 'Joseph Terminal',
  description: 'Open-source multi-venue perpetuals charting terminal.',
};

/** Viewport settings. */
export const viewport: Viewport = {
  themeColor: '#0c1820',
  width: 'device-width',
  initialScale: 1,
};

/** Applies the saved theme before first paint. */
const THEME_BOOT = `try{var t=localStorage.getItem('joseph.theme');if(t==='light'){document.documentElement.dataset.theme='light'}}catch(e){}`;

/**
 * Root layout.
 * @param props Children.
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
        <script dangerouslySetInnerHTML={{__html: THEME_BOOT}} />
      </head>
      <body>{children}</body>
    </html>
  );
}
