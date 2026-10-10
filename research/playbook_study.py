"""Backtest of the smart-money playbook (crypto): sweep -> displacement -> retest -> target the next liquidity pool, 4h trend bias.
Uses the same function the live bot uses (tradebot/playbook.py: crypto_setup) on 2 years of 15-minute candles, 4 coins, entry at the next open,
costs 0.08% per side, stop first on ties, 16-bar time stop. Footprint/liquidity checks are live-only and not included.   Run: py research/playbook_study.py"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import data, playbook

COST = 0.0008
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def run(sym, rr_min, part):
    c15 = data.load_csv(os.path.join(data.DATA_DIR, "futures_%s_15m.csv" % sym))
    c4 = data._resample(c15, data.TF_MS["4h"])
    cut = int(len(c15) * 0.6)
    lo, hi = (300, cut) if part == "is" else (cut, len(c15) - 20)
    cfg = dict(playbook.DEFAULTS, rr_min=rr_min)
    t4 = [x[0] for x in c4]
    res, free, j4 = [], 0, 0
    for i in range(max(lo, 300), hi):
        if i < free:
            continue
        t = c15[i][0]
        while j4 + 1 < len(t4) and t4[j4 + 1] + 4 * 3600000 <= t + 900000:    # only 4h bars that have fully closed by this 15m close
            j4 += 1
        if t4[j4] + 4 * 3600000 > t + 900000:
            continue
        setup, _ = playbook.crypto_setup(c15[i - 299:i + 1], c4[max(0, j4 - 119):j4 + 1], cfg)
        if not setup:
            continue
        sd, stop, tgt = setup["side"], setup["stop"], setup["target"]
        entry = c15[i + 1][1] * (1 + COST * sd)
        risk = abs(entry - stop)
        if risk <= 0 or (sd == 1 and stop >= entry) or (sd == -1 and stop <= entry):
            continue
        ex, end = None, min(len(c15) - 1, i + 1 + cfg["max_hold_bars"])
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
        ex *= (1 - COST * sd)
        res.append(sd * (ex - entry) / risk)
        free = end + 1
    return res


t0 = time.time()
for rr in (1.5, 2.0, 3.0):
    row = []
    for part in ("is", "oos"):
        r = [x for s in SYMS for x in run(s, rr, part)]
        days = (len(data.load_csv(os.path.join(data.DATA_DIR, "futures_BTCUSDT_15m.csv"))) * (0.6 if part == "is" else 0.4)) / 96
        row.append("%s: n=%d (%.2f/day over 4 coins) win %2.0f%% avg %+.2fR" % (part.upper(), len(r), len(r) / days, sum(1 for x in r if x > 0) / max(1, len(r)) * 100, sum(r) / max(1, len(r))))
    print("rr_min %.1f | %s   [%.0fs]" % (rr, " | ".join(row), time.time() - t0), flush=True)
