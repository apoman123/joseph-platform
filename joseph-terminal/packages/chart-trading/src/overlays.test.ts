/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Tests for overlay view models and drag interpretation.
 */

import {describe, expect, it} from 'vitest';
import {defaultTicketInputs} from './intent-builder';
import {
  DRAFT_LAYER_ID,
  ORDERS_LAYER_ID,
  buildDraftLayer,
  buildOrderLayer,
  buildPositionLayer,
  interpretDrag,
} from './overlays';
import {BTC, order, position} from './testing/fixtures';

const PRECISION = {tickSize: BTC.tick_size, lotSize: BTC.lot_size};

describe('buildOrderLayer', () => {
  it('renders unknown orders as unknown, never canceled', () => {
    const layer = buildOrderLayer(
      [order({state: 'UNKNOWN_RECONCILING'})],
      BTC.instrument_id,
      PRECISION,
      true
    );
    const line = layer.models[0];
    expect(line?.kind).toBe('price-line');
    if (line?.kind !== 'price-line') return;
    expect(line.tone).toBe('warning');
    expect(line.pending).toBe(true);
    expect(line.label).toContain('Unknown, reconciling');
    expect(line.draggable).toBe(false);
  });

  it('excludes terminal orders and other instruments', () => {
    const layer = buildOrderLayer(
      [
        order({state: 'CANCELED'}),
        order({order_id: 'x', instrument_id: 'mock:perp:ETHUSD'}),
      ],
      BTC.instrument_id,
      PRECISION,
      true
    );
    expect(layer.models).toHaveLength(0);
  });

  it('labels partial fills and allows dragging working orders', () => {
    const layer = buildOrderLayer(
      [order({state: 'PARTIALLY_FILLED', filled_base: '0.004'})],
      BTC.instrument_id,
      PRECISION,
      true
    );
    const line = layer.models[0];
    if (line?.kind !== 'price-line') throw new Error('expected line');
    expect(line.label).toBe('Buy 0.010 (0.004 filled) · Partially filled');
    expect(line.draggable).toBe(true);
    expect(line.tone).toBe('bid');
  });

  it('styles stop-loss and take-profit children', () => {
    const layer = buildOrderLayer(
      [
        order({
          order_id: 'sl',
          role: 'STOP_LOSS',
          side: 'sell',
          order_type: 'stop_market',
          limit_price: null,
          trigger_price: '90000',
        }),
      ],
      BTC.instrument_id,
      PRECISION,
      false
    );
    const line = layer.models[0];
    if (line?.kind !== 'price-line') throw new Error('expected line');
    expect(line.tone).toBe('danger');
    expect(line.price).toBe('90000');
    expect(line.draggable).toBe(false);
  });
});

describe('buildPositionLayer', () => {
  it('draws entry and liquidation lines', () => {
    const layer = buildPositionLayer(
      [position()],
      BTC.instrument_id,
      PRECISION
    );
    expect(layer.models.map(m => m.id)).toEqual(['pos-1:entry', 'pos-1:liq']);
  });
});

describe('buildDraftLayer', () => {
  it('uses the user tone for authored lines', () => {
    const layer = buildDraftLayer({
      ...defaultTicketInputs(BTC.instrument_id, null),
      limitPrice: '95000',
      stopLoss: '94000',
    });
    expect(layer.models.map(m => m.id)).toEqual(['draft-limit', 'draft-sl']);
    expect(
      layer.models.every(m => m.kind === 'price-line' && m.tone === 'user')
    ).toBe(true);
  });
});

describe('interpretDrag', () => {
  it('maps draft drags to ticket fields with tick rounding', () => {
    expect(
      interpretDrag(DRAFT_LAYER_ID, 'draft-sl', 94123.456, BTC.tick_size, [])
    ).toEqual({
      kind: 'draft',
      field: 'stopLoss',
      price: '94123.5',
    });
  });

  it('turns order drags into amend drafts that still need confirmation', () => {
    const intent = interpretDrag(
      ORDERS_LAYER_ID,
      'ord-1',
      94250.02,
      BTC.tick_size,
      [order()]
    );
    expect(intent).toMatchObject({
      kind: 'amend',
      field: 'limit_price',
      from: '94000',
      price: '94250',
    });
  });

  it('ignores unknown layers and no-op drags', () => {
    expect(interpretDrag('other', 'x', 1, '0.1', []).kind).toBe('ignore');
    expect(
      interpretDrag(ORDERS_LAYER_ID, 'ord-1', 94000, '0.1', [order()]).kind
    ).toBe('ignore');
  });
});
