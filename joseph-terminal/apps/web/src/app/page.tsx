/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Home route (`/`): the terminal, loaded on the client only.
 */

import type {ReactElement} from 'react';
import {ClientTerminal} from './client-terminal';

/**
 * Renders the terminal at the home route.
 * @return Page.
 */
export default function Page(): ReactElement {
  return <ClientTerminal />;
}
