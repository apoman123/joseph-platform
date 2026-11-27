// TradeX library (2026-11-25): popular indicators written as TradeX's own Pine Script (AGPL with this project, no
// TradingView code or calls). Shown first in the Indicators dialog; added like a script from the Pine Editor.
window.TX_PINE_LIB = [
  { id: "rsi", name: "Relative Strength Index (RSI)", sub: "Momentum, 0-100", source: `//@version=5
indicator("RSI", overlay=false)
len = input.int(14, "Length")
r = ta.rsi(close, len)
plot(r, "RSI", color=color.new(#b4bcbf, 0), linewidth=2)
hline(70, "Overbought", color=color.new(#797e86, 0))
hline(30, "Oversold", color=color.new(#797e86, 0))` },
  { id: "macd", name: "MACD", sub: "Trend and momentum", source: `//@version=5
indicator("MACD", overlay=false)
fast = input.int(12, "Fast")
slow = input.int(26, "Slow")
sig = input.int(9, "Signal")
[m, s, h] = ta.macd(close, fast, slow, sig)
plot(h, "Histogram", color=h >= 0 ? color.new(#46a76f, 30) : color.new(#d65b4b, 30), style=plot.style_histogram)
plot(m, "MACD", color=color.new(#e8ecee, 0))
plot(s, "Signal", color=color.new(#797e86, 0))` },
  { id: "bb", name: "Bollinger Bands", sub: "Volatility bands on price", source: `//@version=5
indicator("Bollinger Bands", overlay=true)
len = input.int(20, "Length")
mult = input.float(2.0, "Std dev")
[mid, up, lo] = ta.bb(close, len, mult)
plot(mid, "Basis", color=color.new(#797e86, 0))
plot(up, "Upper", color=color.new(#b4bcbf, 0))
plot(lo, "Lower", color=color.new(#b4bcbf, 0))` },
  { id: "emacross", name: "EMA Cross 9 / 21", sub: "Fast and slow trend lines", source: `//@version=5
indicator("EMA Cross", overlay=true)
f = input.int(9, "Fast")
s = input.int(21, "Slow")
plot(ta.ema(close, f), "Fast EMA", color=color.new(#46a76f, 0), linewidth=2)
plot(ta.ema(close, s), "Slow EMA", color=color.new(#d65b4b, 0), linewidth=2)` },
  { id: "ribbon", name: "Moving Average Ribbon", sub: "EMA 20 / 50 / 100 / 200", source: `//@version=5
indicator("MA Ribbon", overlay=true)
plot(ta.ema(close, 20), "EMA 20", color=color.new(#e8ecee, 0))
plot(ta.ema(close, 50), "EMA 50", color=color.new(#b4bcbf, 0))
plot(ta.ema(close, 100), "EMA 100", color=color.new(#797e86, 0))
plot(ta.ema(close, 200), "EMA 200", color=color.new(#46a76f, 0), linewidth=2)` },
  { id: "supertrend", name: "Supertrend", sub: "Trend line that flips with ATR", source: `//@version=5
indicator("Supertrend", overlay=true)
factor = input.float(3.0, "Factor")
atrLen = input.int(10, "ATR length")
[st, dir] = ta.supertrend(factor, atrLen)
plot(dir < 0 ? st : na, "Up trend", color=color.new(#46a76f, 0), linewidth=2, style=plot.style_linebr)
plot(dir > 0 ? st : na, "Down trend", color=color.new(#d65b4b, 0), linewidth=2, style=plot.style_linebr)` },
  { id: "vwap", name: "VWAP", sub: "Volume-weighted average price (daily)", source: `//@version=5
indicator("VWAP", overlay=true)
plot(ta.vwap(hlc3), "VWAP", color=color.new(#e8ecee, 0), linewidth=2)` },
  { id: "stochrsi", name: "Stochastic RSI", sub: "Faster momentum, 0-100", source: `//@version=5
indicator("Stoch RSI", overlay=false)
r = ta.rsi(close, 14)
k = ta.sma(ta.stoch(r, r, r, 14), 3)
d = ta.sma(k, 3)
plot(k, "K", color=color.new(#e8ecee, 0))
plot(d, "D", color=color.new(#797e86, 0))
hline(80, "Upper", color=color.new(#797e86, 0))
hline(20, "Lower", color=color.new(#797e86, 0))` },
  { id: "atr", name: "Average True Range (ATR)", sub: "How much price moves per bar", source: `//@version=5
indicator("ATR", overlay=false)
len = input.int(14, "Length")
plot(ta.atr(len), "ATR", color=color.new(#b4bcbf, 0), linewidth=2)` },
  { id: "cci", name: "Commodity Channel Index (CCI)", sub: "Distance from the average", source: `//@version=5
indicator("CCI", overlay=false)
len = input.int(20, "Length")
plot(ta.cci(hlc3, len), "CCI", color=color.new(#b4bcbf, 0), linewidth=2)
hline(100, "Upper", color=color.new(#797e86, 0))
hline(-100, "Lower", color=color.new(#797e86, 0))` },
  { id: "adx", name: "ADX / DMI", sub: "Trend strength and direction", source: `//@version=5
indicator("ADX DMI", overlay=false)
len = input.int(14, "Length")
[dip, dim, adx] = ta.dmi(len, len)
plot(adx, "ADX", color=color.new(#e8ecee, 0), linewidth=2)
plot(dip, "+DI", color=color.new(#46a76f, 0))
plot(dim, "-DI", color=color.new(#d65b4b, 0))` },
  { id: "donchian", name: "Donchian Channels", sub: "Highest high and lowest low", source: `//@version=5
indicator("Donchian Channels", overlay=true)
len = input.int(20, "Length")
up = ta.highest(high, len)
lo = ta.lowest(low, len)
plot(up, "Upper", color=color.new(#46a76f, 0))
plot((up + lo) / 2, "Middle", color=color.new(#797e86, 0))
plot(lo, "Lower", color=color.new(#d65b4b, 0))` },
  { id: "keltner", name: "Keltner Channels", sub: "EMA with ATR bands", source: `//@version=5
indicator("Keltner Channels", overlay=true)
len = input.int(20, "Length")
mult = input.float(2.0, "ATR multiplier")
basis = ta.ema(close, len)
rng = ta.atr(10)
plot(basis, "Basis", color=color.new(#797e86, 0))
plot(basis + mult * rng, "Upper", color=color.new(#b4bcbf, 0))
plot(basis - mult * rng, "Lower", color=color.new(#b4bcbf, 0))` },
  { id: "obv", name: "On-Balance Volume (OBV)", sub: "Volume flowing in or out", source: `//@version=5
indicator("OBV", overlay=false)
obv = ta.cum(math.sign(ta.change(close)) * volume)
plot(obv, "OBV", color=color.new(#b4bcbf, 0), linewidth=2)` },
];
