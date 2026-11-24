/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Accessible popover anchored to a trigger button.
 *
 * The trigger is a button with `aria-haspopup="dialog"`, `aria-expanded`
 * and `aria-controls`; the panel is a labelled `role="dialog"`. Escape or a
 * press outside closes it. Focus stays where it is: the panel's controls
 * follow the trigger in the tab order.
 */

'use client';
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
  type ReactElement,
} from 'react';

/** Popover props. */
interface PopoverProps {
  /** Trigger content. */
  label: ReactNode;
  /** Accessible name of the popover dialog. */
  ariaLabel: string;
  /**
   * Accessible name of the trigger when its content is icon-only. Omit when
   * the trigger shows text, so the name matches the visible label.
   */
  triggerLabel?: string;
  /** Extra trigger class. */
  className?: string;
  /** Popover body; receives a close callback. */
  children: (close: () => void) => ReactNode;
  /** Whether to align the panel to the right edge of the trigger. */
  alignRight?: boolean;
  /** Test id for the trigger. */
  testId?: string;
}

/**
 * Renders a trigger button and, while open, its popover panel.
 * @param props Props.
 * @return Popover element.
 */
export function Popover({
  label,
  ariaLabel,
  triggerLabel,
  className,
  children,
  alignRight,
  testId,
}: PopoverProps): ReactElement {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      // Pointer events on the document always target a DOM node.
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return (
    <div className="popover" ref={root}>
      <button
        type="button"
        className={className ?? 'j-btn j-btn--ghost'}
        aria-label={triggerLabel}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={id}
        data-testid={testId}
        onClick={() => void setOpen(value => !value)}
      >
        {label}
      </button>
      {open && (
        <div
          id={id}
          className={`popover__panel j-menu ${alignRight ? 'popover__panel--right' : ''}`}
          role="dialog"
          aria-label={ariaLabel}
        >
          {children(() => void setOpen(false))}
        </div>
      )}
    </div>
  );
}
