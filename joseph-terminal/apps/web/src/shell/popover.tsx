/**
 * @fileoverview Accessible popover anchored to a trigger button.
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
  /** Align the panel to the right edge of the trigger. */
  alignRight?: boolean;
  /** Test id for the trigger. */
  testId?: string;
}

/**
 * Popover that closes on outside click and Escape.
 * @param props Props.
 * @return Popover.
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
        onClick={() => setOpen(value => !value)}
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
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}
