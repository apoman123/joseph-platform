/**
 * @fileoverview Status bar, toasts and command palette.
 */
'use client';
import {filterPalette} from '@joseph/terminal-shell';
import {useEffect, useMemo, useRef, useState, type ReactElement} from 'react';
import {formatUtcTime} from './format';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';
import {SlotInline} from './slot-host';

/**
 * UTC clock updated every second.
 * @return Clock text.
 */
function UtcClock() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="statusbar__item j-tabular" aria-label="UTC time">
      {formatUtcTime(now, true)}
    </span>
  );
}

/**
 * Status bar with venue health, mode, clock and attribution.
 * @return Footer.
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
 * Toast stack.
 * @return Toasts.
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
 * Command palette dialog.
 * @return Palette or null.
 */
export function CommandPalette(): ReactElement | null {
  const runtime = useRuntime();
  const state = useStore(runtime.palette);
  const input = useRef<HTMLInputElement>(null);
  const hotkeyHints = useMemo(() => {
    const hints = new Map<string, string>();
    for (const [combo, id] of runtime.hotkeys.bindings()) hints.set(id, combo);
    return hints;
  }, [runtime, state.open]); // eslint-disable-line react-hooks/exhaustive-deps
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
    [runtime, state.query, state.open, hotkeyHints] // eslint-disable-line react-hooks/exhaustive-deps
  );
  useEffect(() => {
    if (state.open) input.current?.focus();
  }, [state.open]);
  if (!state.open) return null;
  const close = () => runtime.palette.update(s => ({...s, open: false}));
  const runAt = (index: number) => {
    const entry = entries[index];
    if (!entry) return;
    close();
    void runtime.commands
      .execute(entry.id)
      .catch((error: unknown) =>
        runtime.notifier.notify(
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
        onPointerDown={e => e.stopPropagation()}
      >
        <input
          ref={input}
          className="j-input palette__input"
          placeholder="Type a command, e.g. four charts or trendline"
          aria-label="Search commands"
          value={state.query}
          onChange={e =>
            runtime.palette.update(s => ({
              ...s,
              query: e.target.value,
              index: 0,
            }))
          }
          onKeyDown={e => {
            if (e.key === 'Escape') close();
            else if (e.key === 'ArrowDown')
              runtime.palette.update(s => ({
                ...s,
                index: (s.index + 1) % Math.max(1, entries.length),
              }));
            else if (e.key === 'ArrowUp')
              runtime.palette.update(s => ({
                ...s,
                index:
                  (s.index - 1 + entries.length) % Math.max(1, entries.length),
              }));
            else if (e.key === 'Enter') runAt(state.index);
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
                  runtime.palette.update(s => ({...s, index}))
                }
                onClick={() => runAt(index)}
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
