/**
 * @fileoverview Exact decimal arithmetic on contract `DecimalString`s.
 *
 * Prices and quantities cross the wire as strings. Any value that may end up
 * in an order (quantity, limit price, trigger) must be manipulated with these
 * helpers, never with IEEE-754 numbers. `toNumber` exists only for display
 * and chart coordinates.
 */

/** Matches the contract `DecimalString` pattern. */
const DECIMAL_PATTERN = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/;

/** Maximum fractional digits produced by division helpers. */
const DIVISION_SCALE = 18;

/**
 * Immutable exact decimal: `units / 10^scale`.
 */
export class Decimal {
  /**
   * Creates a decimal from scaled integer units.
   * @param units Integer value scaled by `10^scale`.
   * @param scale Number of fractional digits (>= 0).
   */
  private constructor(
    readonly units: bigint,
    readonly scale: number
  ) {}

  /**
   * Parses a contract decimal string.
   * @param text Decimal text such as `"94700.5"`.
   * @return The parsed decimal.
   * @throws {RangeError} If the text is not a valid DecimalString.
   */
  static parse(text: string): Decimal {
    const trimmed = text.trim();
    if (!DECIMAL_PATTERN.test(trimmed)) {
      throw new RangeError(`Not a decimal string: ${text}`);
    }
    const negative = trimmed.startsWith('-');
    const body = negative ? trimmed.slice(1) : trimmed;
    const [whole, fraction = ''] = body.split('.');
    const units = BigInt(`${whole}${fraction}`);
    return new Decimal(negative ? -units : units, fraction.length);
  }

  /**
   * Converts a finite JavaScript number using its shortest round-trip text.
   * Intended for pointer/chart coordinates, never for user-typed quantities.
   * @param value Finite number.
   * @param maxScale Maximum fractional digits to keep (rounded half-even).
   * @return The decimal.
   * @throws {RangeError} If the value is not finite.
   */
  static fromNumber(value: number, maxScale = 12): Decimal {
    if (!Number.isFinite(value)) {
      throw new RangeError(`Not a finite number: ${value}`);
    }
    const text = value.toFixed(Math.min(maxScale, 20));
    return Decimal.parse(text).normalize();
  }

  /**
   * Returns zero.
   * @return The decimal zero.
   */
  static zero(): Decimal {
    return new Decimal(0n, 0);
  }

  /**
   * Checks whether a string is a valid contract decimal.
   * @param text Candidate text.
   * @return True when `Decimal.parse` would succeed.
   */
  static isValid(text: string): boolean {
    return DECIMAL_PATTERN.test(text.trim());
  }

  /**
   * Rescales to at least `scale` fractional digits without losing value.
   * @param scale Target scale (>= current scale).
   * @return Equivalent decimal with the requested scale.
   */
  private withScale(scale: number): Decimal {
    if (scale <= this.scale) return this;
    return new Decimal(this.units * 10n ** BigInt(scale - this.scale), scale);
  }

  /**
   * Adds another decimal.
   * @param other Addend.
   * @return The exact sum.
   */
  add(other: Decimal): Decimal {
    const scale = Math.max(this.scale, other.scale);
    return new Decimal(
      this.withScale(scale).units + other.withScale(scale).units,
      scale
    );
  }

  /**
   * Subtracts another decimal.
   * @param other Subtrahend.
   * @return The exact difference.
   */
  sub(other: Decimal): Decimal {
    const scale = Math.max(this.scale, other.scale);
    return new Decimal(
      this.withScale(scale).units - other.withScale(scale).units,
      scale
    );
  }

  /**
   * Multiplies by another decimal.
   * @param other Multiplier.
   * @return The exact product.
   */
  mul(other: Decimal): Decimal {
    return new Decimal(this.units * other.units, this.scale + other.scale);
  }

  /**
   * Divides by another decimal, truncating toward zero at 18 digits.
   * @param other Non-zero divisor.
   * @return The quotient.
   * @throws {RangeError} On division by zero.
   */
  div(other: Decimal): Decimal {
    if (other.units === 0n) throw new RangeError('Division by zero');
    const numerator = this.units * 10n ** BigInt(DIVISION_SCALE + other.scale);
    const denominator = other.units * 10n ** BigInt(this.scale);
    return new Decimal(numerator / denominator, DIVISION_SCALE).normalize();
  }

  /**
   * Compares with another decimal.
   * @param other Value to compare against.
   * @return -1, 0 or 1.
   */
  cmp(other: Decimal): -1 | 0 | 1 {
    const scale = Math.max(this.scale, other.scale);
    const a = this.withScale(scale).units;
    const b = other.withScale(scale).units;
    if (a < b) return -1;
    return a > b ? 1 : 0;
  }

  /**
   * Checks equality by value (scale-insensitive).
   * @param other Value to compare against.
   * @return True when numerically equal.
   */
  eq(other: Decimal): boolean {
    return this.cmp(other) === 0;
  }

  /**
   * Returns whether the value is zero.
   * @return True for zero.
   */
  isZero(): boolean {
    return this.units === 0n;
  }

  /**
   * Returns whether the value is strictly negative.
   * @return True for negative values.
   */
  isNegative(): boolean {
    return this.units < 0n;
  }

  /**
   * Returns the absolute value.
   * @return Non-negative decimal.
   */
  abs(): Decimal {
    return this.units < 0n ? new Decimal(-this.units, this.scale) : this;
  }

  /**
   * Rounds down (toward negative infinity) to a multiple of `step`.
   * Use for quantities so rounding never increases exposure.
   * @param step Positive increment such as a lot size.
   * @return Largest multiple of `step` that is <= this value.
   * @throws {RangeError} If `step` is not positive.
   */
  floorToStep(step: Decimal): Decimal {
    if (step.units <= 0n) throw new RangeError('Step must be positive');
    const scale = Math.max(this.scale, step.scale);
    const value = this.withScale(scale).units;
    const increment = step.withScale(scale).units;
    let multiples = value / increment;
    if (value < 0n && value % increment !== 0n) multiples -= 1n;
    return new Decimal(multiples * increment, scale).normalize();
  }

  /**
   * Rounds to the nearest multiple of `step`, ties to even multiples.
   * Use for limit and trigger prices.
   * @param step Positive increment such as a tick size.
   * @return Nearest multiple of `step`.
   * @throws {RangeError} If `step` is not positive.
   */
  roundToStep(step: Decimal): Decimal {
    const floor = this.floorToStep(step);
    const remainder = this.sub(floor);
    const doubled = remainder.add(remainder);
    const comparison = doubled.cmp(step);
    if (comparison < 0) return floor;
    if (comparison > 0) return floor.add(step).normalize();
    const multiples = floor.div(step);
    const even = multiples.floorToStep(Decimal.parse('2')).eq(multiples);
    return even ? floor : floor.add(step).normalize();
  }

  /**
   * Checks whether the value is an exact multiple of `step`.
   * @param step Positive increment.
   * @return True when aligned to the increment.
   */
  isMultipleOf(step: Decimal): boolean {
    if (step.units <= 0n) return false;
    return this.floorToStep(step).eq(this);
  }

  /**
   * Removes trailing fractional zeros.
   * @return Equivalent decimal with minimal scale.
   */
  normalize(): Decimal {
    let {units, scale} = this;
    while (scale > 0 && units % 10n === 0n) {
      units /= 10n;
      scale -= 1;
    }
    return new Decimal(units, scale);
  }

  /**
   * Formats as a contract `DecimalString` without exponent notation.
   * @return Canonical decimal text.
   */
  toString(): string {
    const {units, scale} = this.normalize();
    const negative = units < 0n;
    const digits = (negative ? -units : units)
      .toString()
      .padStart(scale + 1, '0');
    const whole = digits.slice(0, digits.length - scale);
    const fraction = scale > 0 ? `.${digits.slice(digits.length - scale)}` : '';
    const text = `${whole}${fraction}`;
    return negative && text !== '0' ? `-${text}` : text;
  }

  /**
   * Formats with a fixed number of fractional digits (half-even rounding).
   * @param digits Fractional digits to show.
   * @return Fixed-point text for display.
   */
  toFixed(digits: number): string {
    const step =
      digits === 0
        ? Decimal.parse('1')
        : Decimal.parse(`0.${'0'.repeat(digits - 1)}1`);
    const rounded = this.roundToStep(step).withScale(digits);
    const negative = rounded.units < 0n;
    const raw = (negative ? -rounded.units : rounded.units)
      .toString()
      .padStart(digits + 1, '0');
    const whole = raw.slice(0, raw.length - digits);
    const fraction = digits > 0 ? `.${raw.slice(raw.length - digits)}` : '';
    return `${negative && rounded.units !== 0n ? '-' : ''}${whole}${fraction}`;
  }

  /**
   * Converts to a JavaScript number for display or chart coordinates only.
   * @return Nearest IEEE-754 number.
   */
  toNumber(): number {
    return Number(this.toString());
  }
}

/**
 * Returns the number of fractional digits implied by a step such as `0.01`.
 * @param step Tick or lot size string.
 * @return Count of fractional digits (0 for integer steps).
 */
export function stepDecimals(step: string): number {
  return Decimal.parse(step).normalize().scale;
}
