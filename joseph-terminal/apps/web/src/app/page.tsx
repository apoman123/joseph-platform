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
