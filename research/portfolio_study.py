"""Shared-account portfolio test of the 4h trend systems, on coins the settings were NOT tuned on.
The earlier single-market study (research/study2.py) used 12 coins; here the same fixed settings are run on every downloaded coin with the live
engine's rules (shared equity, max open positions, leverage cap, fees + slippage + funding) over the last 40% of history.
Run: py research/portfolio_study.py"""
import sys, os, json, time, glob
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import backtest as B, data

ORDER = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "SUIUSDT"]
TUNED = set(ORDER)
CFG = {
    "donch20": ("donchian", dict(n=20, trend=200, stop=5.0, trail=1, reverse=1)),
    "donch40": ("donchian", dict(n=40, trend=0, stop=4.0, trail=1, reverse=1)),
    "donch80": ("donchian", dict(n=80, trend=200, stop=5.0, trail=1, reverse=1)),
    "mtf1d": ("mtf_break", dict(n=20, htf="1d", fast=20, slow=50, adx=18, stop=3.0, trail=1, reverse=0)),
    "ema20_150": ("emacross", dict(fast=20, slow=150, adx=15, stop=4.5, trail=1, reverse=1)),
}


def load4h(sym):
    p = os.path.join(data.DATA_DIR, "futures_%s_1h.csv" % sym)
    return data._resample(data.load_csv(p), data.TF_MS["4h"]) if os.path.exists(p) else None


def main():
    syms = [os.path.basename(f)[len("futures_"):-len("_1h.csv")] for f in glob.glob(os.path.join(data.DATA_DIR, "futures_*_1h.csv"))]
    syms = [s for s in ORDER if s in syms] + sorted(s for s in syms if s not in ORDER)
    cs = {s: load4h(s) for s in syms}
    t0 = max(c[0][0] for s, c in cs.items() if s in ORDER)
    t1 = min(c[-1][0] for c in cs.values())
    split = t0 + int((t1 - t0) * 0.6)
    print("%d coins; out-of-sample window %s .. %s" % (len(syms), time.strftime("%Y-%m-%d", time.gmtime(split / 1000)), time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000))), flush=True)
    res = {}
    for name, (strat, p) in CFG.items():
        for label, pool in (("12 tuned coins", [s for s in syms if s in TUNED]), ("%d fresh coins only" % len([s for s in syms if s not in TUNED]), [s for s in syms if s not in TUNED]), ("all %d" % len(syms), syms)):
            for mp in (6, 12):
                uni = [(s, cs[s], strat, p) for s in pool]
                m = B.portfolio_run(uni, "4h", risk=0.005, max_pos=mp, max_lev=3.0, start=split, end=t1)
                if not m:
                    continue
                wk = m["years"] * 52.18
                print("%-10s %-20s maxpos %2d | ret %+6.1f%%/yr %+6.1f%% mdd %4.1f%% sharpe %+.2f PF %.2f win %2.0f%% | %4d trades = %.1f/week" % (
                    name, label, mp, m["ret"] * 100, m["cagr"] * 100, m["mdd"] * 100, m["sharpe"], m["pf"], m["win"] * 100, m["trades"], m["trades"] / wk), flush=True)
                res["%s|%s|%d" % (name, label, mp)] = {k: m[k] for k in ("ret", "cagr", "mdd", "sharpe", "pf", "win", "trades", "years")}
    json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_study.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
