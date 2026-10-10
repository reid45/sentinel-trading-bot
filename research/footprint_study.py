"""Does following 'big player' positioning predict the next move? Binance publishes (30-day) history of: top-trader long/short POSITION ratio,
all-accounts long/short ratio, taker buy/sell volume ratio and open interest. We test each against the next 1h and 4h return on 12 coins.
Information coefficient (rank correlation) and a t-statistic on NON-overlapping samples. Run: py research/footprint_study.py"""
import sys, os, json, time, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import data

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "SUIUSDT"]
B = "https://fapi.binance.com"


def hist(path, sym, period="1h"):
    out, end = [], int(time.time() * 1000)
    for _ in range(2):
        data._pace_wait(0.5)
        d = data._get("%s%s?symbol=%s&period=%s&limit=500&endTime=%d" % (B, path, sym, period, end))
        if not d:
            break
        out = d + out
        end = d[0]["timestamp"] - 1
    seen, rows = set(), []
    for r in out:
        if r["timestamp"] not in seen:
            seen.add(r["timestamp"])
            rows.append(r)
    return sorted(rows, key=lambda r: r["timestamp"])


def rank(x):
    s = sorted(range(len(x)), key=lambda i: x[i])
    r = [0.0] * len(x)
    for k, i in enumerate(s):
        r[i] = k
    return r


def corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb) if va and vb else 0.0


def main():
    feats = {k: [] for k in ("top_level", "top_change_3h", "crowd_level", "taker_level", "oi_chg_x_price")}
    fwd = {"1h": [], "4h": []}
    xs = {}                                                     # time -> list of (feature dict, fwd1, fwd4): cross-sections for the market-neutral test
    for sym in COINS:
        try:
            top = hist("/futures/data/topLongShortPositionRatio", sym)
            crowd = hist("/futures/data/globalLongShortAccountRatio", sym)
            taker = hist("/futures/data/takerlongshortRatio", sym)
            oi = hist("/futures/data/openInterestHist", sym)
            px = {r[0]: r[4] for r in data.fetch_recent("futures", sym, "1h", 900)}
        except Exception as e:
            print("skip", sym, str(e)[:60])
            continue
        T = lambda rows, key: {r["timestamp"] // 3600000 * 3600000: float(r[key]) for r in rows}
        a, b, c, d = T(top, "longShortRatio"), T(crowd, "longShortRatio"), T(taker, "buySellRatio"), T(oi, "sumOpenInterestValue")
        ts = sorted(set(a) & set(b) & set(c) & set(d) & set(px))
        for i in range(168, len(ts) - 4, 4):                       # every 4h: non-overlapping 4h forward windows
            t = ts[i]
            if ts[i + 4] - t != 4 * 3600000 or ts[i - 3] - ts[i - 4] != 3600000:
                continue
            z = lambda m: (m[t] - sum(m[x] for x in ts[i - 168:i]) / 168) / ((sum((m[x] - sum(m[y] for y in ts[i - 168:i]) / 168) ** 2 for x in ts[i - 168:i]) / 168) ** 0.5 or 1)
            fv = dict(top_level=z(a), top_change_3h=a[t] / a[ts[i - 3]] - 1, crowd_level=z(b), taker_level=z(c), oi_chg_x_price=(d[t] / d[ts[i - 3]] - 1) * (1 if px[t] > px[ts[i - 3]] else -1))
            xs.setdefault(t, []).append((fv, px[ts[i + 1]] / px[t] - 1, px[ts[i + 4]] / px[t] - 1))
            feats["top_level"].append(z(a))
            feats["top_change_3h"].append(a[t] / a[ts[i - 3]] - 1)
            feats["crowd_level"].append(z(b))
            feats["taker_level"].append(z(c))
            feats["oi_chg_x_price"].append((d[t] / d[ts[i - 3]] - 1) * (1 if px[t] > px[ts[i - 3]] else -1))
            fwd["1h"].append(px[ts[i + 1]] / px[t] - 1)
            fwd["4h"].append(px[ts[i + 4]] / px[t] - 1)
        print(sym, "ok", flush=True)
    n = len(fwd["4h"])
    print("\nsamples (non-overlapping 4h windows, 12 coins, ~30 days): %d. |IC| needs to exceed about %.2f to be significant at 95%%" % (n, 1.96 / math.sqrt(n)))
    for name, f in feats.items():
        row = []
        for h in ("1h", "4h"):
            ic = corr(rank(f), rank(fwd[h]))
            row.append("next %s IC %+.3f (t=%+.1f)" % (h, ic, ic * math.sqrt(n - 2) / math.sqrt(max(1e-9, 1 - ic * ic))))
        print("  %-16s %s" % (name, " | ".join(row)))
    print("MARKET-NEUTRAL: rank the coins against each other at each 4h step (removes the move everything shares). IC averaged over time:")
    for name in feats:
        out = []
        for hi, h in ((1, "1h"), (2, "4h")):
            ics = []
            for t, rows in xs.items():
                if len(rows) >= 8:
                    ics.append(corr(rank([r[0][name] for r in rows]), rank([r[hi] for r in rows])))
            m = sum(ics) / len(ics)
            sd = (sum((x - m) ** 2 for x in ics) / (len(ics) - 1)) ** 0.5
            out.append("next %s mean IC %+.3f (t=%+.1f over %d steps)" % (h, m, m / (sd / math.sqrt(len(ics))) if sd else 0, len(ics)))
        print("  %-16s %s" % (name, " | ".join(out)))


main()
