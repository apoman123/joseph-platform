# premium_discount_ote

Dealing range with premium, discount, equilibrium and OTE (V5 §6.2-G).
Written from the specification text only; the reference Pine script
carries a third-party CC BY-NC-SA 4.0 notice and was not consulted.

**Settings:** `swing_length` 10, `break_mode` CLOSE | WICK, `ote_low`
0.62, `ote_high` 0.79.

**Semantics**
- Swings are confirmed pivots with `swing_length` bars on each side.
- Breaking the latest unbroken swing high (close or wick per `break_mode`)
  starts a bullish range from the latest swing low to the high
  (`RANGE_RESET`); breaking the latest swing low starts a bearish range.
  The range extends with the leg (new highs in a bullish range, new lows
  in a bearish one) **without changing `range_id`**.
- EQ = 50 %; premium = (EQ, high], discount = [low, EQ). OTE is the
  62–79 % retracement against the leg: below EQ for a bullish leg, above
  EQ for a bearish leg.
- AOIs PREMIUM (bearish side), DISCOUNT (bullish side), EQUILIBRIUM (line)
  and OTE carry `range_id`; on reset the previous range's AOIs are
  invalidated.

**Values:** `range_id`, `direction`, `range_high`, `range_low`, `eq`,
`ote_lower`, `ote_upper`, `in_premium`, `in_discount`, `in_ote` (for the
bar close; `None` before the first range).

**ASSUMPTIONS**
- Any newer confirmed swing becomes the break reference, so breaking a
  minor higher low inside a bullish leg resets to a bearish range.
- An outside bar breaking both swings resolves by its body direction.
- Stale anchors that do not span a valid range (high ≤ low) do not reset
  the range (found by property testing).
- Prices are rounded to 8 decimals for publication.
