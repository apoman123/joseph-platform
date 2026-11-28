/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Hover card of a heatmap tile: the group › industry header,
 * the symbol with its description, price, exact size and colour values,
 * and the other symbols of the same industry (or group) with their colour
 * values.
 */

'use client';
import type {HeatmapMetric} from '@joseph/contracts';
import type {ReactElement} from 'react';
import {
  formatMetric,
  formatPrice,
  metricValue,
  type PreparedTile,
} from './model';
import {badgeLetter} from './render';
import styles from './heatmap.module.css';

/** Tooltip props. */
export interface TooltipProps {
  /** DOM id (the hovered tile is described by it). */
  id: string;
  /** Hovered tile. */
  target: PreparedTile;
  /** Peers listed below. */
  peers: readonly PreparedTile[];
  /** Fill colour per prepared tile. */
  fillOf: (tile: PreparedTile) => string;
  /** Size metric. */
  size: HeatmapMetric | undefined;
  /** Colour metric. */
  color: HeatmapMetric | undefined;
  /** Currency. */
  currency: string;
  /** Anchor point relative to the map (CSS px). */
  x: number;
  /** Anchor point relative to the map (CSS px). */
  y: number;
  /** Map width, to keep the card inside. */
  width: number;
  /** Map height, to keep the card inside. */
  height: number;
}

const CARD_W = 280;

/**
 * Renders the hover card.
 * @param props Tooltip props.
 * @return Positioned tooltip.
 */
export function HeatmapTooltip({
  id,
  target,
  peers,
  fillOf,
  size,
  color,
  currency,
  x,
  y,
  width,
  height,
}: TooltipProps): ReactElement {
  const tile = target.tile;
  const estimated = 150 + peers.length * 22;
  const left = x + 16 + CARD_W > width ? Math.max(4, x - CARD_W - 16) : x + 16;
  const top = Math.max(4, Math.min(y + 12, height - estimated - 4));
  const sizeText =
    size && size.id !== 'mono'
      ? formatMetric(size, metricValue(tile, size.id), currency)
      : null;
  const colorText = formatMetric(color, target.color, currency);
  const header = [tile.group, tile.industry].filter(Boolean).join(' › ');
  return (
    <div
      id={id}
      role="tooltip"
      className={styles.tooltip}
      style={{left, top, width: CARD_W}}
    >
      {header && <div className={styles.tooltipHeader}>{header}</div>}
      <div className={styles.tooltipMain}>
        <span
          className={styles.tooltipBadge}
          style={{background: fillOf(target)}}
          aria-hidden="true"
        >
          {badgeLetter(tile.symbol)}
        </span>
        <div className={styles.tooltipName}>
          <strong>{tile.symbol}</strong>
          <span>{tile.name ?? ''}</span>
        </div>
        <strong className={styles.tooltipChange}>{colorText}</strong>
      </div>
      <dl className={styles.tooltipFacts}>
        <dt>Price</dt>
        <dd>{formatPrice(tile.price, currency)}</dd>
        {sizeText !== null && size && (
          <>
            <dt>{size.label}</dt>
            <dd>{sizeText}</dd>
          </>
        )}
        {color && (
          <>
            <dt>{color.label}</dt>
            <dd>{colorText}</dd>
          </>
        )}
        {tile.exchange && (
          <>
            <dt>Exchange</dt>
            <dd>{tile.exchange}</dd>
          </>
        )}
      </dl>
      {peers.length > 0 && (
        <ul className={styles.tooltipPeers} aria-label="Related symbols">
          {peers.map(peer => (
            <li key={peer.tile.key}>
              <span
                className={styles.tooltipSwatch}
                style={{background: fillOf(peer)}}
                aria-hidden="true"
              />
              <span className={styles.tooltipPeerSymbol}>
                {peer.tile.symbol}
              </span>
              <span className={styles.tooltipPeerName}>
                {peer.tile.name ?? ''}
              </span>
              <span>{formatMetric(color, peer.color, currency)}</span>
            </li>
          ))}
        </ul>
      )}
      <div className={styles.tooltipFoot}>
        {tile.error ? `${tile.error} · ` : ''}
        {tile.instrument_id
          ? 'Click to open the chart · right-click for more'
          : (tile.chart_unavailable_reason ?? 'No chart on configured venues')}
      </div>
    </div>
  );
}
