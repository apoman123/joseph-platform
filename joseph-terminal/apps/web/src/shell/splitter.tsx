/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Accessible drag handle (`role="separator"`) for resizing
 * docks and chart-grid tracks with the pointer or the arrow keys.
 */

'use client';
import {useRef, type CSSProperties, type ReactElement} from 'react';

/** Splitter props. */
export interface SplitterProps {
  /** `vertical` separates columns (drag left/right). */
  orientation: 'vertical' | 'horizontal';
  /** Accessible name. */
  label: string;
  /** Current value for assistive tech (percent or pixels). */
  value: number;
  /** Minimum value. */
  min: number;
  /** Maximum value. */
  max: number;
  /** Called with the pointer delta in pixels while dragging. */
  onDrag: (deltaPx: number) => void;
  /** Called once when a drag or key press ends. */
  onCommit?: () => void;
  /** Step in pixels for arrow keys. */
  keyStepPx?: number;
  /** Extra class. */
  className?: string;
  /** Inline position. */
  style?: CSSProperties;
  /** Test id. */
  testId?: string;
}

/**
 * Drag handle.
 * @param props Props.
 * @return Separator element.
 */
export function Splitter({
  orientation,
  label,
  value,
  min,
  max,
  onDrag,
  onCommit,
  keyStepPx = 16,
  className,
  style,
  testId,
}: SplitterProps): ReactElement {
  const last = useRef<number | null>(null);
  const axis = (event: {clientX: number; clientY: number}) =>
    orientation === 'vertical' ? event.clientX : event.clientY;
  return (
    <div
      role="separator"
      tabIndex={0}
      aria-label={label}
      aria-orientation={orientation}
      aria-valuenow={Math.round(value)}
      aria-valuemin={Math.round(min)}
      aria-valuemax={Math.round(max)}
      className={`splitter splitter--${orientation} ${className ?? ''}`}
      style={style}
      data-testid={testId}
      onPointerDown={event => {
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        last.current = axis(event);
      }}
      onPointerMove={event => {
        if (last.current === null) return;
        const position = axis(event);
        const delta = position - last.current;
        if (delta !== 0) {
          last.current = position;
          onDrag(delta);
        }
      }}
      onPointerUp={event => {
        if (last.current === null) return;
        last.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
        onCommit?.();
      }}
      onKeyDown={event => {
        const back = orientation === 'vertical' ? 'ArrowLeft' : 'ArrowUp';
        const forward = orientation === 'vertical' ? 'ArrowRight' : 'ArrowDown';
        if (event.key !== back && event.key !== forward) return;
        event.preventDefault();
        onDrag(event.key === forward ? keyStepPx : -keyStepPx);
        onCommit?.();
      }}
    />
  );
}
