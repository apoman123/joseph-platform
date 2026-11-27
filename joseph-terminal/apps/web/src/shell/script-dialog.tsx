/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/** @fileoverview Accessible focused dialog for script source and settings. */

'use client';
import {useEffect, useRef, type ReactElement, type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {X} from 'lucide-react';
import styles from './script-ui.module.css';

/**
 * Traps focus in a named modal, closes on Escape and restores the opener.
 * @param props Accessible title, contents and close callback.
 * @return Modal portal.
 */
export function ScriptDialog({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}): ReactElement {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement;
    root.current
      ?.querySelector<HTMLElement>('button, input, textarea')
      ?.focus();
    return () => {
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);
  return createPortal(
    <div
      className={styles.backdrop}
      onPointerDown={event => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={root}
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onKeyDown={event => {
          event.stopPropagation();
          if (event.key === 'Escape') {
            event.preventDefault();
            onClose();
          }
          if (event.key !== 'Tab') return;
          const nodes = [
            ...(root.current?.querySelectorAll<HTMLElement>(
              'button:not(:disabled), input:not(:disabled), textarea, select, [tabindex="0"]'
            ) ?? []),
          ];
          const first = nodes[0];
          const last = nodes.at(-1);
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last?.focus();
          }
          if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first?.focus();
          }
        }}
      >
        <header className={styles.header}>
          <h2>{title}</h2>
          <button
            type="button"
            className="j-btn j-btn--ghost j-btn--icon"
            aria-label={`Close ${title}`}
            onClick={onClose}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>
        {children}
      </div>
    </div>,
    document.body
  );
}
