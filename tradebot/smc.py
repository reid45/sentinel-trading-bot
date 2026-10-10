"""Smart-money / ICT style setups, written as plain causal rules (nothing here sees a bar before it has closed).

  sweep  liquidity sweep reversal: price pokes through a swing high/low where stop orders sit, then closes back inside
  fvg    fair value gap retest: an impulsive 3-candle gap is revisited and rejected, trade resumes the impulse
  ob     order block retest: the last opposing candle before a break of structure is revisited and rejected

Every strategy returns (side, dist, stop_px): side +1/-1 decided at the close of the bar, dist = stop distance from that close,
stop_px = the exact structure stop (beyond the sweep wick / zone), so the backtester and the live engine place the same stop.
A setup is only emitted when the stop is wide enough that fees cannot eat the trade (`mind`) and not absurdly wide (`maxd`)."""
from . import indicators as I

ROUND_TRIP = 0.0016          # about 0.16%: the fee + slippage drag the setups must clear


def _cols(c):
    return [x[1] for x in c], [x[2] for x in c], [x[3] for x in c], [x[4] for x in c]


def _ok_session(t, sess):
    if not sess:
        return True
    hr = (t // 3600000) % 24
    return 7 <= hr < 21 if sess == 1 else (7 <= hr < 11 or 12 <= hr < 16) if sess == 2 else True


def _trend_ok(side, cl, tr, mode):
    """mode 0 off, 1 trade with the EMA trend, -1 against it."""
    if not mode or tr is None:
        return True
    return (cl > tr) == (side == 1) if mode == 1 else (cl > tr) != (side == 1)


def _gate(side, close, stop, a, p):
    """Stop-distance gates. -> distance or None."""
    d = (close - stop) * side
    if d <= 0 or not a:
        return None
    if d < p.get("mind", 0.003) * close or d > p.get("maxd", 3.5) * a:
        return None
    return d


def sweep(c, p):
    o, h, l, cl = _cols(c)
    n = len(c)
    a = I.atr(h, l, cl, 14)
    tr = I.ema(cl, p["trend"]) if p.get("trend") else None
    vol = [x[5] for x in c]
    vavg = I.sma(vol, 20) if p.get("vol") else None
    vmin = p.get("vol", 0)
    k, age, wick, pen, buf, conf = p.get("k", 3), p.get("age", 80), p.get("wick", 0.35), p.get("pen", 1.5), p.get("buf", 0.15), p.get("conf", 0)
    side, dist, spx = [0] * n, [None] * n, [None] * n
    hi, lo = [], []                    # unswept swing levels: [price, bar]
    pend = []                          # sweeps waiting for a structure shift when conf>0: [dir, extreme, bar, sweep_bar_low/high]
    for i in range(n):
        if a[i] is None:
            continue
        hi = [x for x in hi if i - x[1] <= age]
        lo = [x for x in lo if i - x[1] <= age]
        sig = None
        if vavg and (vavg[i - 1] is None or vol[i] < vmin * vavg[i - 1]):
            hi = [x for x in hi if x[0] >= h[i]]       # no volume climax: levels still get consumed, but no trade
            lo = [x for x in lo if x[0] <= l[i]]
            j = i - k
            if j >= k:
                if h[j] > max(h[j - k:j]) and h[j] >= max(h[j + 1:i + 1]):
                    hi.append([h[j], j])
                if l[j] < min(l[j - k:j]) and l[j] <= min(l[j + 1:i + 1]):
                    lo.append([l[j], j])
            continue
        # --- sweep of buy-side liquidity (above swing highs) -> short
        broken = [x for x in hi if h[i] > x[0]]
        if broken:
            lv = max(x[0] for x in broken)
            top = h[i]
            rej = (top - max(o[i], cl[i])) >= wick * (top - l[i]) if top > l[i] else False
            equal = sum(1 for x in hi if abs(x[0] - lv) <= 0.15 * a[i]) >= 2 if p.get("eq") else True
            if cl[i] < lv and (top - lv) <= pen * a[i] and rej and equal:
                if conf:
                    pend.append([-1, top, i, l[i]])
                else:
                    sig = (-1, top)
            hi = [x for x in hi if x[0] >= h[i]]
        broken = [x for x in lo if l[i] < x[0]]
        if broken:
            lv = min(x[0] for x in broken)
            bot = l[i]
            rej = (min(o[i], cl[i]) - bot) >= wick * (h[i] - bot) if h[i] > bot else False
            equal = sum(1 for x in lo if abs(x[0] - lv) <= 0.15 * a[i]) >= 2 if p.get("eq") else True
            if cl[i] > lv and (lv - bot) <= pen * a[i] and rej and equal:
                if conf:
                    pend.append([1, bot, i, h[i]])
                else:
                    sig = (1, bot)
            lo = [x for x in lo if x[0] <= l[i]]
        if conf and pend and sig is None:                   # structure shift: close through the sweep candle's far side within `conf` bars
            keep = []
            for pe in pend:
                if i - pe[2] > conf:
                    continue
                if i > pe[2] and ((pe[0] == -1 and cl[i] < pe[3]) or (pe[0] == 1 and cl[i] > pe[3])) and sig is None:
                    ext = max(pe[1], max(h[pe[2]:i + 1])) if pe[0] == -1 else min(pe[1], min(l[pe[2]:i + 1]))
                    sig = (pe[0], ext)
                else:
                    keep.append(pe)
            pend = keep
        if sig:
            sd, ext = sig
            stop = ext + buf * a[i] * (1 if sd == -1 else -1)
            d = _gate(sd, cl[i], stop, a[i], p)
            if d and _trend_ok(sd, cl[i], tr[i] if tr else None, p.get("tw", 0)) and _ok_session(c[i][0], p.get("sess", 0)):
                side[i], dist[i], spx[i] = sd, d, stop
                pend = []
        j = i - k                                         # register the swing point that just got confirmed
        if j >= k:
            if h[j] > max(h[j - k:j]) and h[j] >= max(h[j + 1:i + 1]):
                hi.append([h[j], j])
            if l[j] < min(l[j - k:j]) and l[j] <= min(l[j + 1:i + 1]):
                lo.append([l[j], j])
    return side, dist, spx


def _zones(c, p, kind):
    """Yield-free helper: list of zones born per bar. zone = [dir, top, bottom, born_bar]."""
    o, h, l, cl = _cols(c)
    n = len(c)
    a = I.atr(h, l, cl, 14)
    births = [[] for _ in range(n)]
    gmin, disp, m = p.get("gmin", 0.3), p.get("disp", 1.0), p.get("bos", 10)
    for i in range(max(m, 3), n):
        if a[i] is None:
            continue
        if kind == "fvg":
            body = abs(cl[i - 1] - o[i - 1])
            if body >= disp * a[i] and l[i] - h[i - 2] >= gmin * a[i] and cl[i - 1] > o[i - 1]:
                births[i].append([1, l[i], h[i - 2], i])
            elif body >= disp * a[i] and l[i - 2] - h[i] >= gmin * a[i] and cl[i - 1] < o[i - 1]:
                births[i].append([-1, l[i - 2], h[i], i])
        else:                                            # order block: last opposing candle before a displacement that breaks structure
            body = abs(cl[i] - o[i])
            if body < disp * a[i]:
                continue
            if cl[i] > o[i] and cl[i] > max(h[i - m:i]):
                for j in range(i - 1, max(i - 5, 0), -1):
                    if cl[j] < o[j]:
                        births[i].append([1, h[j], l[j], i])
                        break
            elif cl[i] < o[i] and cl[i] < min(l[i - m:i]):
                for j in range(i - 1, max(i - 5, 0), -1):
                    if cl[j] > o[j]:
                        births[i].append([-1, h[j], l[j], i])
                        break
    return births, a


def _retest(c, p, kind):
    o, h, l, cl = _cols(c)
    n = len(c)
    births, a = _zones(c, p, kind)
    tr = I.ema(cl, p["trend"]) if p.get("trend") else None
    age, buf = p.get("age", 40), p.get("buf", 0.2)
    side, dist, spx = [0] * n, [None] * n, [None] * n
    live = []
    for i in range(n):
        if a[i] is None:
            continue
        live = [z for z in live if i - z[3] <= age]
        fired = None
        keep = []
        for z in live:
            d, top, bot = z[0], z[1], z[2]
            if (d == 1 and cl[i] < bot) or (d == -1 and cl[i] > top):
                continue                                  # zone failed: closed through it
            touched = (d == 1 and l[i] <= top) or (d == -1 and h[i] >= bot)
            mid = (top + bot) / 2
            if touched and i > z[3] and fired is None and ((d == 1 and cl[i] > mid) or (d == -1 and cl[i] < mid)):
                fired = z
                continue
            keep.append(z)
        live = keep
        if fired:
            sd = fired[0]
            stop = (min(fired[2], l[i]) - buf * a[i]) if sd == 1 else (max(fired[1], h[i]) + buf * a[i])
            dd = _gate(sd, cl[i], stop, a[i], p)
            if dd and _trend_ok(sd, cl[i], tr[i] if tr else None, p.get("tw", 0)) and _ok_session(c[i][0], p.get("sess", 0)):
                side[i], dist[i], spx[i] = sd, dd, stop
        live += births[i]
    return side, dist, spx


def fvg(c, p):
    return _retest(c, p, "fvg")


def ob(c, p):
    return _retest(c, p, "ob")


def describe(kind, side, level=None):
    return {"sweep": "swept liquidity %s and closed back inside" % ("above the highs" if side == -1 else "below the lows"),
            "fvg": "retested a fair value gap and rejected it", "ob": "retested an order block and rejected it"}.get(kind, kind)
