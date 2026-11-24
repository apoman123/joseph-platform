/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Pop-out window route (`/popout?pane=…&ws=…`): one chart or
 * dock panel detached from the main terminal window.
 */

import type {Metadata} from 'next';
import type {ReactElement} from 'react';
import {ClientPopout} from './client-popout';

/** Page metadata (the window sets a pane-specific title at runtime). */
export const metadata: Metadata = {
  title: 'Joseph Terminal window',
};

/**
 * Pop-out page.
 * @return Page.
 */
export default function PopoutPage(): ReactElement {
  return <ClientPopout />;
}
