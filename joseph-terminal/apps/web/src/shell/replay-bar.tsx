/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Bar replay controls shown inside a chart pane.
 */

'use client';
import type {ReplayState} from '@joseph/terminal-shell';
import {
  ChevronLeft,
  ChevronRight,
  Pause,
  Play,
  SkipForward,
  X,
} from 'lucide-react';
import type {ReactElement} from 'react';
import {formatUtcTime} from './format';

/** Replay bar props. */
export interface ReplayBarProps {
  /** Replay state. */
  state: ReplayState;
  /** Visible bar count. */
  shown: number;
  /** Total bar count. */
  total: number;
  /** Whether the cursor is on the newest bar. */
  atEnd: boolean;
  /** Steps by bars. */
  onStep: (delta: number) => void;
  /** Toggles playback. */
  onPlayPause: () => void;
  /** Cycles speed. */
  onSpeed: () => void;
  /** Starts at the default lookback. */
  onStartDefault: () => void;
  /** Leaves replay. */
  onExit: () => void;
}

/**
 * Replay controls.
 * @param props Props.
 * @return Toolbar.
 */
export function ReplayBar({
  state,
  shown,
  total,
  atEnd,
  onStep,
  onPlayPause,
  onSpeed,
  onStartDefault,
  onExit,
}: ReplayBarProps): ReactElement {
  const playing = state.phase === 'playing';
  return (
    <div
      className="replay-bar"
      role="toolbar"
      aria-label="Bar replay"
      data-testid="replay-bar"
      data-phase={state.phase}
    >
      <span className="replay-bar__badge">Replay</span>
      {state.phase === 'select' ? (
        <>
          <span className="replay-bar__hint">
            Click a bar to replay from it, or
          </span>
          <button
            type="button"
            className="j-btn j-btn--sm"
            data-testid="replay-start-default"
            onClick={onStartDefault}
          >
            Start 100 bars back
          </button>
        </>
      ) : (
        <>
          <button
            type="button"
            className="j-btn j-btn--sm j-btn--ghost j-btn--icon"
            aria-label="Step back one bar"
            disabled={playing}
            onClick={() => void onStep(-1)}
          >
            <ChevronLeft size={15} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="j-btn j-btn--sm j-btn--icon"
            aria-label={playing ? 'Pause replay' : 'Play replay'}
            data-testid="replay-play"
            disabled={atEnd && !playing}
            onClick={onPlayPause}
          >
            {playing ? (
              <Pause size={15} aria-hidden="true" />
            ) : (
              <Play size={15} aria-hidden="true" />
            )}
          </button>
          <button
            type="button"
            className="j-btn j-btn--sm j-btn--ghost j-btn--icon"
            aria-label="Step forward one bar"
            data-testid="replay-step"
            disabled={atEnd || playing}
            onClick={() => void onStep(1)}
          >
            <ChevronRight size={15} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="j-btn j-btn--sm j-btn--ghost"
            aria-label={`${state.speed}× speed (bars per second): change speed`}
            data-testid="replay-speed"
            onClick={onSpeed}
          >
            <SkipForward size={14} aria-hidden="true" />
            {state.speed}×
          </button>
          <span className="replay-bar__pos j-tabular" data-testid="replay-pos">
            {state.cursorMs !== null && formatUtcTime(state.cursorMs, false)} ·
            bar {shown} of {total}
          </span>
        </>
      )}
      <span className="replay-bar__note">Trading is off while replaying</span>
      <button
        type="button"
        className="j-btn j-btn--sm j-btn--ghost"
        data-testid="replay-exit"
        onClick={onExit}
      >
        <X size={14} aria-hidden="true" />
        Back to live
      </button>
    </div>
  );
}
