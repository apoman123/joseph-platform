/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Terminal route.
 */

import type {ReactElement} from 'react';
import {ClientTerminal} from './client-terminal';

/**
 * Home page rendering the terminal.
 * @return Page.
 */
export default function Page(): ReactElement {
  return <ClientTerminal />;
}
