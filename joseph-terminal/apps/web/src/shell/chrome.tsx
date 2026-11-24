/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Status bar, toasts and command palette.
 *
 * The palette is a modal `role="dialog"`. Its search input takes focus on
 * open and keeps it: Up/Down move the highlighted option (`aria-selected`,
 * wrapping at the ends), Enter runs it, Escape or a press outside closes
 * the palette. Toasts are announced politely through `role="status"`.
 */

'use client';
import {filterPalette} from '@joseph/terminal-shell';
import {useEffect, useMemo, useRef, useState, type ReactElement} from 'react';
import {formatUtcTime} from './format';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {SlotInline} from './slot-host';

/**
 * Renders a UTC clock that updates every second.
 * @return Clock element.
 */
function UtcClock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => void setNow(Date.now()), 1000);
    return () => void clearInterval(timer);
  }, []);
  return (
    <span className="statusbar__item j-tabular" aria-label="UTC time">
      {formatUtcTime(now, true)}
    </span>
  );
}

/**
 * Renders the status bar: status-bar slot items (venue health), the DEMO
 * notice, the clock and the attribution links.
 * @return Footer element.
 */
export function StatusBar(): ReactElement {
  const runtime = useRuntime();
  return (
    <footer className="statusbar">
      <SlotInline slot="status-bar" />
      {runtime.clients.demo && (
        <span className="statusbar__item statusbar__demo">
          Demo mode: synthetic prices, simulated paper broker
        </span>
      )}
      <span className="statusbar__grow" />
      <UtcClock />
      <a
        className="statusbar__item"
        href="https://www.tradingview.com/lightweight-charts/"
        target="_blank"
        rel="noreferrer"
      >
        Charts by TradingView Lightweight Charts™
      </a>
      <a className="statusbar__item" href="/notices">
        Licenses
      </a>
    </footer>
  );
}

/**
 * Renders the toast stack.
 * @return Live region holding the current toasts.
 */
export function Toasts(): ReactElement {
  const runtime = useRuntime();
  const toasts = useStore(runtime.toasts);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map(toast => (
        <div key={toast.id} className={`toast toast--${toast.tone}`}>
          {toast.message}
        </div>
      ))}
    </div>
  );
}

/**
 * Renders the command palette dialog while it is open.
 * @return Palette, or null while closed.
 */
export function CommandPalette(): ReactElement | null {
  const runtime = useRuntime();
  const state = useStore(runtime.palette);
  const input = useRef<HTMLInputElement>(null);
  // The registries are mutable, not observable: `state.open` makes the
  // palette re-read bindings and commands each time it opens.
  const hotkeyHints = useMemo(() => {
    const hints = new Map<string, string>();
    for (const [combo, id] of runtime.hotkeys.bindings()) {
      hints.set(id, combo);
    }
    return hints;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `state.open` re-reads the mutable registries each time the palette opens
  }, [runtime, state.open]);
  const entries = useMemo(
    () =>
      filterPalette(
        runtime.commands.list().map(c => ({
          id: c.id,
          title: c.title,
          shortcut: hotkeyHints.get(c.id),
        })),
        state.query
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `state.open` re-reads the mutable registries each time the palette opens
    [runtime, state.query, state.open, hotkeyHints]
  );
  useEffect(() => {
    if (state.open) input.current?.focus();
  }, [state.open]);
  if (!state.open) return null;
  const close = () => void runtime.palette.update(s => ({...s, open: false}));
  const runAt = (index: number) => {
    const entry = entries[index];
    if (!entry) return;
    close();
    void runtime.commands
      .execute(entry.id)
      .catch(
        (error: unknown) =>
          void runtime.notifier.notify(
            error instanceof Error ? error.message : 'Command failed',
            'error'
          )
      );
  };
  return (
    <div className="palette-backdrop" onPointerDown={close}>
      <div
        className="palette j-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onPointerDown={e => void e.stopPropagation()}
      >
        <input
          ref={input}
          className="j-input palette__input"
          placeholder="Type a command, e.g. four charts or trendline"
          aria-label="Search commands"
          value={state.query}
          onChange={e =>
            void runtime.palette.update(s => ({
              ...s,
              query: e.target.value,
              index: 0,
            }))
          }
          onKeyDown={e => {
            if (e.key === 'Escape') close();
            else if (e.key === 'ArrowDown') {
              runtime.palette.update(s => ({
                ...s,
                index: (s.index + 1) % Math.max(1, entries.length),
              }));
            } else if (e.key === 'ArrowUp') {
              runtime.palette.update(s => ({
                ...s,
                index:
                  (s.index - 1 + entries.length) % Math.max(1, entries.length),
              }));
            } else if (e.key === 'Enter') runAt(state.index);
            else return;
            e.preventDefault();
          }}
        />
        <ul className="palette__list" role="listbox" aria-label="Commands">
          {entries.map((entry, index) => (
            <li key={entry.id}>
              <button
                type="button"
                role="option"
                aria-selected={index === state.index}
                className="j-menu__item palette__item"
                onPointerEnter={() =>
                  void runtime.palette.update(s => ({...s, index}))
                }
                onClick={() => void runAt(index)}
              >
                <span>{entry.title}</span>
                {entry.shortcut && <kbd>{entry.shortcut}</kbd>}
              </button>
            </li>
          ))}
          {entries.length === 0 && (
            <li className="j-muted palette__empty">
              No command matches “{state.query}”.
            </li>
          )}
        </ul>
      </div>
    </div>
  );
}
