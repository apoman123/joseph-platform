/**
 * @fileoverview Main-window UI of detachable panes: the button that opens a
 * pane in another window, the placeholder left in its slot (focus or bring
 * back the window) and the banner that offers to re-open windows after a
 * reload (browsers only open windows from a click).
 */
'use client';
import {
  detachedStatus,
  paneKey,
  type DetachedPane,
  type DetachedPaneRef,
  type DetachedStatus,
} from '@joseph/terminal-shell';
import {AppWindow, ExternalLink} from 'lucide-react';
import type {ReactElement} from 'react';
import {useStore} from './react-bindings';
import {useRuntime} from './runtime-context';

/** Status lines shown in placeholders. */
const STATUS_TEXT: Readonly<Record<DetachedStatus, string>> = {
  opening: 'Opening the window…',
  live: 'Linked: follows link groups, theme and workspace changes.',
  dormant: 'This pane was in another window before the reload.',
  lost: 'The window stopped responding.',
};

/**
 * Detached pane for a reference, with its current status.
 * @param ref Pane reference.
 * @return Pane and status, or null when docked.
 */
export function useDetached(
  ref: DetachedPaneRef
): {pane: DetachedPane; status: DetachedStatus} | null {
  const runtime = useRuntime();
  const state = useStore(runtime.detached);
  const key = paneKey(ref);
  const pane = state.panes.find(p => p.key === key);
  if (!pane) return null;
  return {pane, status: detachedStatus(state.live[key], Date.now())};
}

/**
 * Icon button that opens a pane in another window.
 * @param props Pane and title.
 * @return Button, or nothing when windows can't be linked.
 */
export function PopOutButton({
  pane,
  title,
  testId,
}: {
  pane: DetachedPaneRef;
  title: string;
  testId: string;
}): ReactElement | null {
  const runtime = useRuntime();
  const windows = useStore(runtime.windows);
  if (!windows?.supported) return null;
  return (
    <button
      type="button"
      className="j-btn j-btn--ghost j-btn--icon j-btn--sm pane__popout"
      aria-label={`Open ${title} in another window`}
      title="Open in another window"
      data-testid={testId}
      onClick={() => windows.detach(pane, title)}
    >
      <ExternalLink size={14} aria-hidden="true" />
    </button>
  );
}

/**
 * Placeholder shown where a detached pane used to be.
 * @param props Pane, status and size variant.
 * @return Placeholder.
 */
export function DetachedPlaceholder({
  pane,
  status,
  compact = false,
}: {
  pane: DetachedPane;
  status: DetachedStatus;
  compact?: boolean;
}): ReactElement {
  const runtime = useRuntime();
  const windows = useStore(runtime.windows);
  const waiting = status === 'dormant' || status === 'lost';
  return (
    <section
      className={`detached ${compact ? 'detached--compact' : ''}`}
      data-status={status}
      data-testid={`detached-${pane.key}`}
      aria-label={`${pane.title} is in another window`}
    >
      <div className="detached__card">
        <span className="detached__glyph" aria-hidden="true">
          <AppWindow size={compact ? 18 : 22} strokeWidth={1.5} />
        </span>
        <div className="detached__text">
          <h2 className="detached__title">Open in another window</h2>
          <p className="detached__pane">{pane.title}</p>
          <p className="detached__status" role="status">
            <span className="detached__dot" aria-hidden="true" />
            {STATUS_TEXT[status]}
          </p>
        </div>
        <div className="detached__actions">
          {waiting ? (
            <button
              type="button"
              className="j-btn j-btn--sm j-btn--primary"
              data-testid="detached-reopen"
              onClick={() => windows?.reopen(pane.key)}
            >
              Re-open window
            </button>
          ) : (
            <button
              type="button"
              className="j-btn j-btn--sm"
              data-testid="detached-focus"
              onClick={() => windows?.focus(pane.key)}
            >
              Focus window
            </button>
          )}
          <button
            type="button"
            className="j-btn j-btn--sm j-btn--ghost"
            data-testid="detached-bring-back"
            onClick={() => windows?.redock(pane.key)}
          >
            Bring back
          </button>
        </div>
      </div>
    </section>
  );
}

/**
 * Banner offering to re-open windows remembered from the saved layout.
 * @return Banner, or nothing when every detached pane has a window.
 */
export function DetachedBanner(): ReactElement | null {
  const runtime = useRuntime();
  const state = useStore(runtime.detached);
  const now = Date.now();
  const dormant = state.panes.filter(
    p => detachedStatus(state.live[p.key], now) === 'dormant'
  );
  const windows = useStore(runtime.windows);
  if (dormant.length === 0 || !windows) return null;
  const names = dormant.map(p => p.title).join(', ');
  return (
    <div
      className="detached-banner"
      role="region"
      aria-label="Detached windows"
      data-testid="detached-banner"
    >
      <AppWindow size={15} aria-hidden="true" />
      <p className="detached-banner__text">
        {dormant.length === 1
          ? `${names} was open in another window.`
          : `${dormant.length} panes were open in other windows: ${names}.`}
      </p>
      <button
        type="button"
        className="j-btn j-btn--sm j-btn--primary"
        data-testid="reopen-detached"
        onClick={() => windows.reopenAll()}
      >
        Re-open detached windows
      </button>
      <button
        type="button"
        className="j-btn j-btn--sm j-btn--ghost"
        onClick={() => {
          for (const pane of dormant) windows.redock(pane.key);
        }}
      >
        Keep here
      </button>
    </div>
  );
}
