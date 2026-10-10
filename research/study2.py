"""Walk-forward strategy search on real Binance perp candles (fees + slippage + funding included).
Parameters are picked on the first 60% of each market ("in-sample") and judged on the last 40% ("out-of-sample"), pooled across markets.
Run: py research/study2.py <tf> <strategy> [risk%]   e.g.  py research/study2.py 1h mtf_break"""
import sys, os, itertools, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import backtest as B, data
from tradebot.strategies import STRATEGIES

TF, NAME = sys.argv[1], sys.argv[2]
RISK = float(sys.argv[3]) / 100 if len(sys.argv) > 3 else 0.005
ALL = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "SUIUSDT")


def load(s):
    path = os.path.join(data.DATA_DIR, "futures_%s_%s.csv" % (s, TF))
    if os.path.exists(path):
        return data.load_csv(path)
    for src in ("1h", "15m"):                      # derive slower candles (4h, 1d) from a faster file
        pp = os.path.join(data.DATA_DIR, "futures_%s_%s.csv" % (s, src))
        if os.path.exists(pp) and data.TF_MS[src] < data.TF_MS[TF]:
            return data._resample(data.load_csv(pp), data.TF_MS[TF])
    return None


CANDLES = {s: load(s) for s in ALL}
CANDLES = {s: c for s, c in CANDLES.items() if c}
SYMS = list(CANDLES)

GRIDS = {
    # name: (signal params, execution params)
    "donchian": (dict(n=[20, 40, 80], trend=[0, 200], stop=[2.0, 3.0, 4.0, 5.0]), dict(trail=[1], reverse=[0, 1])),
    "mtf_break": (dict(n=[20, 40, 80], htf=["4h", "1d"], fast=[20], slow=[50], adx=[0, 18, 25], stop=[2.0, 3.0, 4.0]), dict(trail=[1], reverse=[0])),
    "squeeze": (dict(bb=[20], q=[0.1, 0.2, 0.3], n=[10, 20], htf=["", "4h"], stop=[2.0, 3.0]), dict(trail=[1], reverse=[0])),
    "bbrev": (dict(k=[2.0, 2.5, 3.0], adx=[15, 20, 25], lo=[10, 15, 25], stop=[1.5, 2.5]), dict(rr=[0.8, 1.2, 2.0], maxbars=[8, 24])),
    "pullback": (dict(rsi_n=[2, 3], lo=[10, 20], trend=[100, 200], stop=[2.0, 3.0]), dict(tp=[2.0, 4.0], trail=[0], reverse=[0])),
    "emacross": (dict(fast=[10, 20, 30], slow=[50, 100, 150], adx=[15, 25], stop=[3.0, 4.5]), dict(trail=[1], tp=[0], reverse=[1])),
}


def combos(g):
    keys = list(g)
    return [dict(zip(keys, v)) for v in itertools.product(*[g[k] for k in keys])]


def pooled(res):
    tr = sum(r["trades"] for r in res)
    gp = sum(t[5] for r in res for t in r["trade_list"] if t[5] > 0)
    gl = -sum(t[5] for r in res for t in r["trade_list"] if t[5] <= 0)
    pos = sum(1 for r in res if r["ret"] > 0)
    return dict(ret=sum(r["ret"] for r in res) / len(res), trades=tr, pf=gp / gl if gl else 99, win=sum(1 for r in res for t in r["trade_list"] if t[5] > 0) / tr if tr else 0,
                mdd=max(r["mdd"] for r in res), pos=pos, n=len(res), sharpe=sum(r["sharpe"] for r in res) / len(res))


SIG = {}


def evaluate(sp, ep, part):
    res = []
    for s in SYMS:
        cs = CANDLES[s]
        k = int(len(cs) * 0.6)
        sig = SIG[(tuple(sorted(sp.items())), s)]
        sl = slice(0, k) if part == "is" else slice(k, None)
        res.append(B.run(cs[sl], NAME, dict(sp, **ep), "futures", TF, risk=RISK, sig=tuple(x[sl] for x in sig)))
    return pooled(res)


if __name__ == "__main__":
    sg, eg = GRIDS[NAME]
    t0 = time.time()
    sps = combos(sg)
    for sp in sps:
        for s in SYMS:
            SIG[(tuple(sorted(sp.items())), s)] = STRATEGIES[NAME]["fn"](CANDLES[s], dict(sp, **{k: v[0] for k, v in eg.items()}))
    print("%s %s: %d markets, %d signal sets in %.0fs" % (NAME, TF, len(SYMS), len(sps), time.time() - t0), flush=True)
    rows = []
    for sp in sps:
        for ep in combos(eg):
            m = evaluate(sp, ep, "is")
            if m["trades"] >= 20 * len(SYMS):
                rows.append((m["ret"], sp, ep, m))
    rows.sort(key=lambda r: -r[0])
    print("%d/%d settings profitable in-sample" % (sum(1 for r in rows if r[0] > 0), len(rows)))
    out = []
    for ret, sp, ep, m in rows[:5]:
        o = evaluate(sp, ep, "oos")
        print("  IS %+6.1f%% PF %.2f n=%4d | OOS %+6.1f%% PF %.2f win %2.0f%% n=%4d mdd %2.0f%% sharpe %+.2f up-markets %d/%d | %s %s" % (
            m["ret"] * 100, m["pf"], m["trades"], o["ret"] * 100, o["pf"], o["win"] * 100, o["trades"], o["mdd"] * 100, o["sharpe"], o["pos"], o["n"], sp, ep), flush=True)
        out.append(dict(sp=sp, ep=ep, ins={k: m[k] for k in ("ret", "pf", "trades")}, oos={k: o[k] for k in ("ret", "pf", "win", "trades", "mdd", "sharpe", "pos", "n")}))
    # robustness: how do ALL settings that were profitable in-sample do out-of-sample?
    prof = [r for r in rows if r[0] > 0][:40]
    if prof:
        oo = [evaluate(sp, ep, "oos")["ret"] for _, sp, ep, _ in prof]
        oo.sort()
        print("  all %d in-sample-profitable settings -> OOS median %+.1f%%, %d/%d positive" % (len(prof), oo[len(oo) // 2] * 100, sum(1 for x in oo if x > 0), len(oo)))
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "study2_%s_%s.json" % (NAME, TF)), "w"), indent=1)
    print("%.0fs" % (time.time() - t0))
