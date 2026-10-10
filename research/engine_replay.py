"""Feeds the LIVE engine (sleeves, rules, sizing, stops) the last N bars of real 4h candles for the whole universe, one bar at a time, and
compares it with the shared-account backtester on the same window. A sanity check that the live code path trades like the research said.
Run: py research/engine_replay.py [bars=360]"""
import sys, os, tempfile, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TRADEBOT_DATA"] = tempfile.mkdtemp()
from tradebot import backtest as B, catalog, data
from tradebot.engine import Engine
from tradebot.store import Store

N = int(sys.argv[1]) if len(sys.argv) > 1 else 360
syms = list(catalog.UNIVERSE)
cs = {}
for s in syms:
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "futures_%s_1h.csv" % s)
    if os.path.exists(p):
        cs[s] = data._resample(data.load_csv(p), data.TF_MS["4h"])
syms = [s for s in syms if s in cs]
L = min(len(c) for c in cs.values())
cs = {s: c[-L:] for s, c in cs.items()}
start = L - N
d = tempfile.mkdtemp()
store = Store(path=d + "/t.db", cfg_path=d + "/c.json")
insts = [catalog.make_instrument(catalog.lookup("futures", s) or dict(market="futures", symbol=s, name=s, cat="crypto"), "4h", True) for s in syms]
store.save_cfg({"instruments": insts, "start_equity": 10000.0, "risk": dict(per_trade_pct=0.3, max_positions=15, max_leverage_per_pos=3.0, max_total_leverage=3.0, daily_loss_limit_pct=99, max_drawdown_halt_pct=99),
                "rules": dict(cooldown_min=0, pause_after_stops=0, max_trades_day=0), "ai": dict(mode="off")})
store.put("cash", 10000.0)
k = {"i": start}
eng = Engine(store, fetcher=lambda m, sym, tf, n: cs[sym][:k["i"] + 1][-500:], pricer=lambda m, sym: cs[sym][k["i"]][4])
eng._lot = lambda inst: None                          # exchange minimum sizes are a separate (tested) concern
subs = [(s, {"last_ts": None, "price": None, "signal": 0, "error": None, "price_t": 0}) for i in insts for s in eng._sleeves(i)]
t0 = time.time()
for sub, st in subs:
    eng._process(sub, st)                             # baseline at the start bar
for k["i"] in range(start + 1, L):
    for sub, st in subs:
        eng._process(sub, st)
    if (k["i"] - start) % 60 == 0:
        print("  bar %d/%d  equity %.2f  open %d  closed %d  (%.0fs)" % (k["i"] - start, N, eng.equity(), len(store.positions()), len(store.trades(100000)), time.time() - t0), flush=True)
tr = store.trades(100000)
n_e = len(tr) + len(store.positions())
print("ENGINE : %d trades (%d still open), equity %.2f (%+.2f%%), %.1f trades/week" % (n_e, len(store.positions()), eng.equity(), (eng.equity() / 10000 - 1) * 100, n_e / (N * 4 / 24 / 7)))
members = catalog.defaults_for("futures")[1]["members"]
uni = [(s, cs[s], m["strat"], m["p"]) for m in members for s in syms]
ref = B.portfolio_run(uni, "4h", risk=0.003, max_pos=15, max_lev=3.0, start=cs[syms[0]][start][0])
print("BACKTEST: %d trades, return %+.2f%%, %.1f trades/week (same universe, same window; backtest starts flat with no baseline bars)" % (ref["trades"], ref["ret"] * 100, ref["trades"] / (N * 4 / 24 / 7)))
by = {}
for t in tr:
    by[t["inst"].split("#")[1] if "#" in t["inst"] else "?"] = by.get(t["inst"].split("#")[1] if "#" in t["inst"] else "?", 0) + 1
print("closed trades by sleeve:", by)
errs = [e for e in store.events(200) if e["level"] == "error"]
print("errors logged:", len(errs), [e["msg"][:80] for e in errs[:3]])
