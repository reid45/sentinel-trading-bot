"""Final test of the smart-money day trader on a much bigger sample than the first one: 12 coins x 2 years of 15-minute candles (the first test used 4 coins).
The same live function (tradebot/playbook.py: crypto_setup) finds the setups. Each setup is then simulated under a fixed, pre-declared grid:
  reward target (rr_min) 1.5 / 2 / 3, time stop 16 / 32 / 64 bars, cost per side 0.08% (retail taker + slippage) / 0.05% / 0.02% (maker-like), all trades / longs only / shorts only.
First 60% of each coin = in-sample, last 40% = out-of-sample. Entry at the next open, stop first on ties, one trade per coin at a time.
Because 81 variants are looked at, the number that matter is how many are positive out-of-sample AND in-sample, not the single best.   Run: py research/smart_final.py"""
import sys, os, time, json, math
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import data, playbook

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "SUIUSDT"]
RRS, HOLDS, COSTS = (1.5, 2.0, 3.0), (16, 32, 64), (0.0008, 0.0005, 0.0002)


def detect(args):
    sym, rr = args
    c15 = data.load_csv(os.path.join(data.DATA_DIR, "futures_%s_15m.csv" % sym))
    c4 = data._resample(c15, data.TF_MS["4h"])
    cfg = dict(playbook.DEFAULTS, rr_min=rr)
    t4 = [x[0] for x in c4]
    out, j4 = [], 0
    for i in range(300, len(c15) - 70):
        t = c15[i][0]
        while j4 + 1 < len(t4) and t4[j4 + 1] + 4 * 3600000 <= t + 900000:
            j4 += 1
        if t4[j4] + 4 * 3600000 > t + 900000:
            continue
        setup, _ = playbook.crypto_setup(c15[i - 299:i + 1], c4[max(0, j4 - 119):j4 + 1], cfg)
        if setup:
            out.append((i, setup["side"], setup["stop"], setup["target"]))
    return sym, rr, c15, out


def simulate(c15, setups, hold, cost, part, side_only):
    cut = int(len(c15) * 0.6)
    res, free = [], 0
    for i, sd, stop, tgt in setups:
        if (part == "is") != (i < cut) or i < free or (side_only and sd != side_only):
            continue
        entry = c15[i + 1][1] * (1 + cost * sd)
        risk = abs(entry - stop)
        if risk <= 0 or (sd == 1 and stop >= entry) or (sd == -1 and stop <= entry):
            continue
        ex, end = None, min(len(c15) - 1, i + 1 + hold)
        for k in range(i + 1, end + 1):
            _, o, h, l, cl, _v = c15[k]
            if (sd == 1 and l <= stop) or (sd == -1 and h >= stop):
                ex, end = (min(stop, o) if sd == 1 else max(stop, o)), k
                break
            if (sd == 1 and h >= tgt) or (sd == -1 and l <= tgt):
                ex, end = tgt, k
                break
        if ex is None:
            ex = c15[end][4]
        ex *= (1 - cost * sd)
        res.append(sd * (ex - entry) / risk)
        free = end + 1
    return res


def stat(r):
    n = len(r)
    if n < 2:
        return n, 0.0, 0.0, 0.0
    m = sum(r) / n
    sd = (sum((x - m) ** 2 for x in r) / (n - 1)) ** 0.5
    return n, sum(1 for x in r if x > 0) / n * 100, m, (m / (sd / math.sqrt(n)) if sd else 0.0)


if __name__ == "__main__":
    t0 = time.time()
    with Pool(8) as p:
        found = p.map(detect, [(s, rr) for s in SYMS for rr in RRS])
    print("setups found in %.0fs" % (time.time() - t0), flush=True)
    rows = []
    for rr in RRS:
        for hold in HOLDS:
            for cost in COSTS:
                for so, sl in ((0, "both"), (1, "long"), (-1, "short")):
                    ri, ro = [], []
                    for sym, r2, c15, setups in found:
                        if r2 != rr:
                            continue
                        ri += simulate(c15, setups, hold, cost, "is", so)
                        ro += simulate(c15, setups, hold, cost, "oos", so)
                    rows.append(dict(rr=rr, hold=hold, cost=cost, side=sl, **{"is": stat(ri), "oos": stat(ro)}))
    pos_oos = sum(1 for x in rows if x["oos"][2] > 0)
    both = sum(1 for x in rows if x["oos"][2] > 0 and x["is"][2] > 0)
    print("variants: %d | positive out-of-sample: %d | positive in BOTH halves: %d" % (len(rows), pos_oos, both))
    for tag, cond in (("Retail cost 0.08%/side, all trades", lambda x: x["cost"] == 0.0008 and x["side"] == "both"), ("Cheapest cost 0.02%/side, all trades", lambda x: x["cost"] == 0.0002 and x["side"] == "both")):
        print("\n" + tag)
        for x in rows:
            if cond(x):
                print("  rr %.1f hold %2d | IS n=%4d win %2.0f%% avg %+.2fR | OOS n=%4d win %2.0f%% avg %+.2fR (t %+.1f)" % (x["rr"], x["hold"], x["is"][0], x["is"][1], x["is"][2], x["oos"][0], x["oos"][1], x["oos"][2], x["oos"][3]))
    best = sorted(rows, key=lambda x: -x["oos"][2])[:5]
    print("\nBest five out-of-sample (of %d tried; expect the best of many to look good by luck):" % len(rows))
    for x in best:
        print("  rr %.1f hold %2d cost %.2f%% %s | IS avg %+.2fR (n=%d) | OOS avg %+.2fR (n=%d, t %+.1f)" % (x["rr"], x["hold"], x["cost"] * 100, x["side"], x["is"][2], x["is"][0], x["oos"][2], x["oos"][0], x["oos"][3]))
    json.dump(rows, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "smart_final_results.json"), "w"))
    print("\ndone in %.0fs" % (time.time() - t0))
