"""Compact market-context features for the AI signal filter. Identical code feeds live trading and the historical replay,
so what the replay measures is what live trading gets. No dates or prices-in-absolute-terms leak into the prompt."""
from . import indicators as I


def _pct(a, b):
    return (a / b - 1) * 100 if b else 0.0


def features(hist, side, btc=None, per_day=6, n_break=40):
    """hist: candles up to and including the signal bar (>= 250 bars). btc: BTC candles up to the same bar (or None)."""
    h = hist[-700:]
    cl, hi, lo = [x[4] for x in h], [x[2] for x in h], [x[3] for x in h]
    a = I.atr(hi, lo, cl, 14)
    atr = a[-1] or 1e-9
    e50, e200 = I.ema(cl, 50)[-1] or cl[-1], I.ema(cl, 200)[-1] or cl[-1]
    adx = I.adx(hi, lo, cl, 14)[-1]
    c = cl[-1]
    prior_hi, prior_lo = max(hi[-n_break - 1:-1]), min(lo[-n_break - 1:-1])
    ext = (c - prior_hi) / atr if side == 1 else (prior_lo - c) / atr
    win = min(len(cl), 90 * per_day)
    rng_lo, rng_hi = min(lo[-win:]), max(hi[-win:])
    atr_hist = [x for x in a[-120:] if x]
    f = dict(
        ext=ext, atr_pct=atr / c * 100, vol_ratio=atr / (sum(atr_hist) / len(atr_hist)) if atr_hist else 1.0,
        r1=_pct(c, cl[-1 - per_day]), r7=_pct(c, cl[-1 - 7 * per_day]), r30=_pct(c, cl[-1 - 30 * per_day]) if len(cl) > 30 * per_day else 0.0,
        d50=_pct(c, e50), d200=_pct(c, e200), adx=round(adx) if adx else 0,
        rng=(c - rng_lo) / (rng_hi - rng_lo) * 100 if rng_hi > rng_lo else 50.0,
        last6=[round(_pct(cl[-k], cl[-k - 1]), 1) for k in range(6, 0, -1)],
    )
    if btc is not None and len(btc) > 30 * per_day + 1:
        bc = [x[4] for x in btc]
        f["btc7"], f["btc30"] = _pct(bc[-1], bc[-1 - 7 * per_day]), _pct(bc[-1], bc[-1 - 30 * per_day])
    return f


def render(sym, side, f, extra=""):
    s = "%s %s breakout | ext %+.1f ATR past 40-bar level | ATR %.2f%% (%.1fx its 20d avg) | ret 1d %+.1f%% 7d %+.1f%% 30d %+.1f%% | vs EMA50 %+.1f%% EMA200 %+.1f%% | ADX %d | 90d range pos %d%% | last 6 bars %s" % (
        sym, "LONG" if side == 1 else "SHORT", f["ext"], f["atr_pct"], f["vol_ratio"], f["r1"], f["r7"], f["r30"], f["d50"], f["d200"],
        f["adx"], f["rng"], f["last6"])
    if "btc7" in f:
        s += " | BTC 7d %+.1f%% 30d %+.1f%%" % (f["btc7"], f["btc30"])
    return s + extra
