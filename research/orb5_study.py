"""5-minute opening-range breakout on the last ~60 days of US stocks (all free intraday history Yahoo offers). Small sample: treat as a first look only.
Run: py research/orb5_study.py"""
import sys, os, json, itertools, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import data
from orb_study import UNI, et_offset, COST
import calendar


def load5(sym):
    p = os.path.join(data.DATA_DIR, "stock_%s_5m.json" % sym)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 6 * 3600:
        return json.load(open(p))
    time.sleep(0.4)
    rows = data._yahoo(sym, "5m", 1)
    json.dump(rows, open(p, "w"))
    return rows


def sessions(rows):
    out = {}
    for t, o, h, l, c, v in rows:
        lt = t / 1000 - et_offset(t) * 3600
        g = time.gmtime(lt)
        m = g.tm_hour * 60 + g.tm_min
        if 570 <= m < 960 and g.tm_wday < 5:
            out.setdefault((g.tm_year, g.tm_mon, g.tm_mday), []).append((m, o, h, l, c, v))
    return {d: b for d, b in out.items() if len(b) >= 70 and b[0][0] == 570}


def run(days, p):
    keys, out, hist = sorted(days), [], []
    for d in keys:
        b = days[d]
        n = p["or_min"] // 5
        orb = b[:n]
        hi, lo, vol = max(x[2] for x in orb), min(x[3] for x in orb), sum(x[5] for x in orb)
        h = hist[-10:]
        rv = vol / (sum(h) / len(h)) if len(h) >= 8 and sum(h) else None
        hist.append(vol)
        if rv is None or rv < p["rvol"] or hi <= lo or (hi - lo) / orb[-1][4] < 0.002:
            continue
        for k in range(n, len(b) - 2):
            c = b[k][4]
            sd = 1 if c > hi else -1 if c < lo else 0
            if not sd or (p["dir"] == "long" and sd == -1):
                continue
            entry = b[k + 1][1] * (1 + COST * sd)
            stop = lo if sd == 1 else hi
            risk = abs(entry - stop)
            if risk <= 0 or risk / entry > 0.03:
                break
            tgt = entry + sd * p["tgt"] * risk if p["tgt"] else None
            ex = None
            for j in range(k + 1, len(b)):
                oo, hh, ll = b[j][1], b[j][2], b[j][3]
                if (sd == 1 and ll <= stop) or (sd == -1 and hh >= stop):
                    ex = min(stop, oo) if sd == 1 else max(stop, oo)
                    break
                if tgt and ((sd == 1 and hh >= tgt) or (sd == -1 and ll <= tgt)):
                    ex = tgt
                    break
            ex = (ex if ex is not None else b[-1][4]) * (1 - COST * sd)
            out.append((d, sd * (ex - entry) / entry, sd * (ex - entry) / risk))
            break
    return out


def main():
    allrows = {}
    for s in UNI:
        try:
            allrows[s] = sessions(load5(s))
        except Exception as e:
            print("skip", s, str(e)[:40])
    nd = len({d for v in allrows.values() for d in v})
    print("stocks %d, days %d (5m bars)" % (len(allrows), nd), flush=True)
    res = []
    for p in [dict(or_min=a, rvol=r, dir=d, tgt=t) for a in (5, 15, 30) for r in (0.0, 1.5, 2.5) for d in ("both", "long") for t in (0, 1.0, 2.0)]:
        tr = [x for dd in allrows.values() for x in run(dd, p)]
        if len(tr) < 80:
            continue
        res.append((sum(x[1] for x in tr) / len(tr) * 1e4, sum(x[2] for x in tr) / len(tr), len(tr), sum(1 for x in tr if x[1] > 0) / len(tr) * 100, p))
    res.sort(key=lambda r: -r[0])
    print("%d/%d settings with positive average (no hold-out possible: only ~%d days of data)" % (sum(1 for r in res if r[0] > 0), len(res), nd))
    for bps, R, n, w, p in res[:5]:
        print("  %+6.1f bps/trade  %+.2fR  win %2.0f%%  n=%d | %s" % (bps, R, w, n, p))
    print("  worst:", "%+.1f bps" % res[-1][0])
    # drift-free sanity: the same trades with the entry direction flipped
    print("  median setting: %+.1f bps" % sorted(r[0] for r in res)[len(res) // 2])


main()
