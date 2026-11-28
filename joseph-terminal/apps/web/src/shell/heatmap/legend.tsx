/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Colour legend: one toggle per segment. Pressing a segment
 * hides its tiles, pressing again restores them; segments toggle
 * independently. The "No data" chip toggles tiles without a value.
 */

'use client';
import type {ReactElement} from 'react';
import {textColor, type Legend} from './colors';
import styles from './heatmap.module.css';

/** Segment id of tiles without a colour value. */
export const MISSING_SEGMENT = -1;

/**
 * Renders the legend toggles.
 * @param props Legend, hidden segments, missing colour and toggle callback.
 * @return Legend group.
 */
export function HeatmapLegend({
  legend,
  hidden,
  missing,
  metricLabel,
  onToggle,
}: {
  legend: Legend;
  hidden: ReadonlySet<number>;
  missing: string;
  metricLabel: string;
  onToggle: (segment: number) => void;
}): ReactElement {
  const chips = [
    ...legend.segments.map(segment => ({
      id: segment.index,
      label: segment.label,
      color: segment.color,
    })),
    {id: MISSING_SEGMENT, label: 'No data', color: missing},
  ];
  return (
    <div
      className={styles.legend}
      role="group"
      aria-label={`Colour legend, ${metricLabel}. Press a segment to hide or show its tiles.`}
    >
      {chips.map(chip => (
        <button
          key={chip.id}
          type="button"
          className={styles.legendChip}
          aria-pressed={!hidden.has(chip.id)}
          title={
            hidden.has(chip.id)
              ? `Show ${chip.label} tiles`
              : `Hide ${chip.label} tiles`
          }
          style={
            hidden.has(chip.id)
              ? {borderColor: chip.color}
              : {
                  background: chip.color,
                  borderColor: chip.color,
                  color: textColor(chip.color),
                }
          }
          onClick={() => void onToggle(chip.id)}
        >
          {chip.label}
        </button>
      ))}
    </div>
  );
}
