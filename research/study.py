"""Strategy study: SMC/ICT setups on real Binance perp candles, with fees + slippage + funding, judged out of sample.
Parameters are chosen on the first 60% of each market's history ("in-sample") by pooled result; the last 40% is never used for choosing.
Run: py research/study.py [tf] [strategy,...]"""
import sys, os, itertools, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import backtest as B, data
from tradebot.strategies import STRATEGIES

TF = sys.argv[1] if len(sys.argv) > 1 else "15m"
ONLY = sys.argv[2].split(",") if len(sys.argv) > 2 else ["sweep", "fvg", "ob"]
SYMS = [s for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "SUIUSDT")
        if os.path.exists(os.path.join(data.DATA_DIR, "futures_%s_%s.csv" % (s, TF)))]
def _load(s):
    path = os.path.join(data.DATA_DIR, "futures_%s_%s.csv" % (s, TF))
    if os.path.exists(path):
        return data.load_csv(path)
    return data._resample(data.load_csv(os.path.join(data.DATA_DIR, "futures_%s_15m.csv" % s)), data.TF_MS[TF])      # derive 1h/4h from the 15m file


SYMS = [s for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "SUIUSDT")
        if os.path.exists(os.path.join(data.DATA_DIR, "futures_%s_%s.csv" % (s, TF))) or os.path.exists(os.path.join(data.DATA_DIR, "futures_%s_15m.csv" % s))]
CANDLES = {s: _load(s) for s in SYMS}
RISK = 0.005

SIGNAL_GRID = {
    "sweep": dict(k=[3, 5], wick=[0.3], pen=[1.5], conf=[0, 3], eq=[0, 1], vol=[0, 2.0], trend=[0, 100], tw=[1], mind=[0.003, 0.005]),
    "fvg": dict(gmin=[0.3, 0.6], disp=[1.0, 1.5], trend=[0, 100], tw=[1], mind=[0.003, 0.005], age=[20, 60]),
    "ob": dict(disp=[1.0, 1.5], bos=[10, 20], trend=[0, 100], tw=[1], mind=[0.003, 0.005], age=[20, 60]),
}
EXEC_GRID = dict(rr=[1.5, 2.5, 4.0], be=[0, 1.0], maxbars=[16, 48])


def combos(g):
    keys = list(g)
    return [dict(zip(keys, v)) for v in itertools.product(*[g[k] for k in keys])]


def pooled(res):
    tr = sum(r["trades"] for r in res)
    gp = sum(t[5] for r in res for t in r["trade_list"] if t[5] > 0)
    gl = -sum(t[5] for r in res for t in r["trade_list"] if t[5] <= 0)
    return dict(ret=sum(r["ret"] for r in res) / len(res), trades=tr, pf=gp / gl if gl else 99, win=sum(1 for r in res for t in r["trade_list"] if t[5] > 0) / tr if tr else 0,
                mdd=max(r["mdd"] for r in res))


def evaluate(name, sp, ep, part):
    res = []
    for s in SYMS:
        cs = CANDLES[s]
        k = int(len(cs) * 0.6)
        sig = SIG[(name, tuple(sorted(sp.items())), s)]
        if part == "is":
            res.append(B.run(cs[:k], name, dict(sp, **ep), "futures", TF, risk=RISK, sig=tuple(x[:k] for x in sig)))
        else:
            res.append(B.run(cs[k:], name, dict(sp, **ep), "futures", TF, risk=RISK, sig=tuple(x[k:] for x in sig)))
    return pooled(res)


SIG = {}
out = {}
for name in ONLY:
    t0 = time.time()
    rows = []
    sps = combos(SIGNAL_GRID[name])
    for sp in sps:
        for s in SYMS:
            SIG[(name, tuple(sorted(sp.items())), s)] = STRATEGIES[name]["fn"](CANDLES[s], sp)
    print("%s: %d signal sets built in %.0fs" % (name, len(sps), time.time() - t0), flush=True)
    for sp in sps:
        for ep in combos(EXEC_GRID):
            m = evaluate(name, sp, ep, "is")
            if m["trades"] >= 25 * len(SYMS):
                rows.append((m["ret"], sp, ep, m))
    rows.sort(key=lambda r: -r[0])
    print("== %s top in-sample (pooled, avg return per market at %.1f%% risk) -> out-of-sample" % (name, RISK * 100))
    out[name] = []
    for ret, sp, ep, m in rows[:6]:
        o = evaluate(name, sp, ep, "oos")
        print("  IS %+6.1f%% PF %.2f win %2.0f%% n=%4d | OOS %+6.1f%% PF %.2f win %2.0f%% n=%4d mdd %2.0f%% | %s %s" % (
            m["ret"] * 100, m["pf"], m["win"] * 100, m["trades"], o["ret"] * 100, o["pf"], o["win"] * 100, o["trades"], o["mdd"] * 100, sp, ep), flush=True)
        out[name].append(dict(sp=sp, ep=ep, ins={k: m[k] for k in ("ret", "pf", "win", "trades")}, oos={k: o[k] for k in ("ret", "pf", "win", "trades", "mdd")}))
    pos = sum(1 for r in rows if r[0] > 0)
    print("  %d/%d parameter sets profitable in-sample; %.0fs" % (pos, len(rows), time.time() - t0), flush=True)
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "study_%s.json" % TF), "w"), indent=1)
