/**
 * @license
 * Copyright 2026 Joseph Terminal contributors
 * SPDX-License-Identifier: MIT
 */

/**
 * @fileoverview Position-sizing and reward/risk helpers for the ticket.
 *
 * These are display aids. The server-side Risk Gateway computes the binding
 * verdict for every intent.
 */

import {Decimal, type OrderSide} from '@joseph/contracts';

/** Inputs for risk-based sizing. */
export interface RiskSizingInput {
  /** Account equity in quote currency. */
  equity: Decimal;
  /** Risk per trade in percent of equity, e.g. `0.5`. */
  riskPct: Decimal;
  /** Planned entry price. */
  entry: Decimal;
  /** Stop-loss price. */
  stop: Decimal;
  /** Lot size used to floor the quantity. */
  lotSize: Decimal;
}

/**
 * Computes the base quantity that risks `riskPct` of equity at the stop.
 * The result is floored to the lot size so it never exceeds the budget.
 * @param input Sizing input.
 * @return Quantity, or null when the stop distance is zero.
 */
export function quantityFromRisk(input: RiskSizingInput): Decimal | null {
  const distance = input.entry.sub(input.stop).abs();
  if (distance.isZero() || input.riskPct.isNegative()) return null;
  const budget = input.equity.mul(input.riskPct).div(Decimal.parse('100'));
  return budget.div(distance).floorToStep(input.lotSize);
}

/**
 * Computes the amount lost if the stop is hit.
 * @param quantity Base quantity.
 * @param entry Entry price.
 * @param stop Stop price.
 * @return Quote-currency risk.
 */
export function riskAmount(
  quantity: Decimal,
  entry: Decimal,
  stop: Decimal
): Decimal {
  return quantity.mul(entry.sub(stop).abs());
}

/**
 * Computes reward-to-risk for a planned trade.
 * @param side Entry side.
 * @param entry Entry price.
 * @param stop Stop-loss price.
 * @param target Take-profit price.
 * @return R multiple, or null when the setup is inconsistent.
 */
export function rewardToRisk(
  side: OrderSide,
  entry: Decimal,
  stop: Decimal,
  target: Decimal
): Decimal | null {
  const risk = side === 'buy' ? entry.sub(stop) : stop.sub(entry);
  const reward = side === 'buy' ? target.sub(entry) : entry.sub(target);
  if (risk.isNegative() || risk.isZero() || reward.isNegative()) return null;
  return reward.div(risk);
}
