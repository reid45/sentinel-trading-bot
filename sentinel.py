"""Command-line control for a running Sentinel server (standard library only).
  py sentinel.py status | positions | trades [N] | events [N] | stats
  py sentinel.py start | stop | kill | resume | flatten | close <instrument-id>
  py sentinel.py backtest <SYMBOL> [--market futures|forex] [--strategy donchian] [--years 5] [--optimize]
  py sentinel.py portfolio | ai-test | config [key.path [value]] | broker-test
Set SENTINEL_URL to point at another port (default http://127.0.0.1:8765)."""
import json, os, sys, time, urllib.error, urllib.request

BASE = os.environ.get("SENTINEL_URL", "http://127.0.0.1:8765")


def call(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=120).read())
    except urllib.error.HTTPError as e:
        raise SystemExit("error: " + json.loads(e.read() or b"{}").get("error", str(e)))
    except urllib.error.URLError:
        raise SystemExit("error: server not running at %s (start it with run.bat)" % BASE)


def money(v):
    return ("-" if v < 0 else "") + "$%s" % format(abs(v), ",.2f")


def table(rows, head):
    w = [max(len(str(x)) for x in col) for col in zip(head, *rows)] if rows else [len(h) for h in head]
    print("  ".join(h.ljust(n) for h, n in zip(head, w)))
    for r in rows:
        print("  ".join(str(c).ljust(n) for c, n in zip(r, w)))


def job(kind, body):
    j = call("/api/backtest", dict(body, kind=kind))["job"]
    while True:
        r = call("/api/job?id=" + j)
        if r["state"] == "running":
            print("  ..." + r.get("progress", ""), file=sys.stderr)
            time.sleep(1)
            continue
        if r["state"] == "error":
            raise SystemExit("error: " + r["error"])
        return r["result"]


def show_metrics(label, m):
    print("%-14s return %+7.1f%%  cagr %+6.1f%%  maxDD %5.1f%%  sharpe %5.2f  trades %d" %
          (label, m["ret"] * 100, m["cagr"] * 100, m["mdd"] * 100, m["sharpe"], m["trades"]))


def main(a):
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, rest = a[0], a[1:]
    if cmd in ("status", "stats"):
        s = call("/api/state")["engine"]
        state = "HALTED" if s["halted"] else "running" if s["running"] else "stopped"
        print("engine %s | mode %s | equity %s (%+.2f%%) | today %s | drawdown %.2f%%" % (
            state, s["mode"], money(s["equity"]), s["total_return_pct"], money(s["day_pnl"]), s["drawdown_pct"]))
        print("trades %d | win rate %.0f%% | profit factor %s | open positions %d | daily-loss block: %s" % (
            s["trades"], s["win_rate"], "%.2f" % s["profit_factor"] if s["profit_factor"] else "-", len(s["positions"]), s["daily_block"]))
        errs = [(k, v["error"]) for k, v in s["status"].items() if v.get("error")]
        for k, e in errs:
            print("  ! %s: %s" % (k, e))
    elif cmd == "positions":
        ps = call("/api/state")["engine"]["positions"]
        table([(p["symbol"], "LONG" if p["side"] > 0 else "SHORT", "%.5g" % p["qty"], "%.6g" % p["entry"], "%.6g" % p.get("mark", 0),
                "%.6g" % p["stop"], money(p["upnl"])) for p in ps], ["symbol", "side", "qty", "entry", "mark", "stop", "upnl"]) if ps else print("no open positions")
    elif cmd == "trades":
        n = int(rest[0]) if rest else 20
        ts = call("/api/trades?n=%d" % n)
        table([(time.strftime("%m-%d %H:%M", time.localtime(t["t_close"] / 1000)), t["symbol"], "LONG" if t["side"] > 0 else "SHORT",
                "%.6g" % t["entry"], "%.6g" % t["exit"], money(t["pnl"]), t["reason"]) for t in ts], ["closed", "symbol", "side", "entry", "exit", "pnl", "why"]) if ts else print("no trades yet")
    elif cmd == "events":
        for e in reversed(call("/api/state")["events"][:int(rest[0]) if rest else 20]):
            print(time.strftime("%H:%M:%S", time.localtime(e["t"] / 1000)), e["level"].ljust(6), e["msg"])
    elif cmd in ("start", "stop", "kill", "resume", "flatten"):
        call("/api/engine", {"action": cmd})
        print("ok:", cmd)
    elif cmd == "close":
        call("/api/engine", {"action": "close", "inst": rest[0]})
        print("ok: closed", rest[0])
    elif cmd == "backtest":
        sym = rest[0]
        opt = {rest[i]: rest[i + 1] for i in range(1, len(rest) - 1) if rest[i].startswith("--")}
        mk = opt.get("--market", "forex" if "=X" in sym else "futures")
        strat = opt.get("--strategy", "emacross" if mk == "forex" else "donchian")
        cfg = call("/api/state")["cfg"]
        inst = next((i for i in cfg["instruments"] if i["symbol"] == sym), None)
        body = dict(market=mk, symbol=sym, tf="4h", strategy=strat, years=int(opt.get("--years", 5)), risk=float(opt.get("--risk", cfg["risk"]["per_trade_pct"])),
                    params=inst["params"] if inst else {"n": 40, "trend": 0, "stop": 4.0, "trail": 1, "tp": 0, "reverse": 1})
        r = job("optimize" if "--optimize" in rest else "single", body)
        print("params:", json.dumps(r["params"]))
        show_metrics("full period", r["metrics"])
        show_metrics("out-of-sample", r["oos"])
        print("verdict [%s]: %s" % tuple(r["verdict"]))
    elif cmd == "portfolio":
        r = job("portfolio", {"years": 5})
        show_metrics("full period", r["metrics"])
        show_metrics("first half", r["first_half"])
        show_metrics("second half", r["second_half"])
        print("verdict [%s]: %s" % tuple(r["verdict"]))
    elif cmd == "ai-test":
        r = call("/api/ai/test", {})
        print("OK %d ms, %d tokens: %s" % (r["ms"], r["tokens"], r["reply"]) if r["ok"] else "FAILED: " + r["error"])
    elif cmd == "broker-test":
        r = call("/api/broker/test", {})
        print("OK equity %s" % money(r["equity"]) if r["ok"] else "FAILED: " + r["error"])
    elif cmd == "config":
        cfg = call("/api/state")["cfg"]
        if not rest:
            print(json.dumps({k: v for k, v in cfg.items() if k != "instruments"}, indent=1))
            print("instruments:", ", ".join(i["symbol"] for i in cfg["instruments"] if i["enabled"]))
        else:
            node = cfg
            keys = rest[0].split(".")
            for k in keys[:-1]:
                node = node[k]
            if len(rest) == 1:
                print(node[keys[-1]])
            else:
                old = node[keys[-1]]
                val = type(old)(rest[1]) if not isinstance(old, bool) else rest[1].lower() in ("1", "true", "yes")
                patch = cur = {}
                for k in keys[:-1]:
                    cur[k] = {}
                    cur = cur[k]
                cur[keys[-1]] = val
                call("/api/config", patch)
                print("set %s = %s" % (rest[0], val))
    else:
        raise SystemExit("unknown command; try: py sentinel.py help")


if __name__ == "__main__":
    main(sys.argv[1:])
