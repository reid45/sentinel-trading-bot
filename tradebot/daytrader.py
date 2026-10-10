"""AI day trader. Cheap rules find *candidate* setups on 15-minute candles (breakout, RSI extreme, volume surge); the AI then
decides long / short / flat with a stop and target, using a full dossier (price action, volatility, funding, open interest,
crowding, your open book, its own recent results). Same token discipline as the analyst: static cache-friendly prefix,
dossier last, 2 tool rounds max, hard budgets, circuit breaker, fail-safe (no AI -> no trade)."""
import json, re, time
from . import analyst, context, indicators as I, llm

SYSTEM = (
    "You are the decision engine of an intraday trading bot working 15-minute candles. A rule-based scanner flagged this market as a candidate. "
    "Decide whether to open a trade NOW. Be selective: costs are about 0.16% per round trip, so only take setups where the expected move clearly exceeds costs; "
    "most candidates should be 'flat'. Prefer trading with momentum when ADX is rising and volume confirms; fade only extreme RSI with a clear reversal candle. "
    "Avoid chasing exhausted moves, crowded funding, trading against BTC's trend, and thin/choppy ranges. Always give a protective stop. "
    "Use the dossier; call a tool only if a specific fact is missing (max 2 calls). "
    'Reply with JSON only: {"action":"long|short|flat","conf":<0-100>,"stop_atr":<0.6-3>,"target_atr":<0.8-5>,"why":"<=16 words"}'
)


def candidates(hist):
    """Cheap prefilter on closed bars. Returns a list of reasons (empty = nothing interesting)."""
    if len(hist) < 60:
        return []
    h, l, c, v = [x[2] for x in hist], [x[3] for x in hist], [x[4] for x in hist], [x[5] for x in hist]
    atr = I.atr(h, l, c, 14)[-1] or 0
    out = []
    if c[-1] > max(h[-21:-1]):
        out.append("closed above the 20-bar high (breakout up)")
    elif c[-1] < min(l[-21:-1]):
        out.append("closed below the 20-bar low (breakout down)")
    rsi = I.rsi(c, 14)[-1]
    if rsi is not None and (rsi <= 25 or rsi >= 75):
        out.append("RSI %.0f is extreme" % rsi)
    avg = sum(v[-21:-1]) / 20 if sum(v[-21:-1]) else 0
    if avg and v[-1] >= 2.2 * avg and atr and abs(c[-1] - hist[-1][1]) >= 0.8 * atr:
        out.append("volume %.1fx average on a strong candle" % (v[-1] / avg))
    return out


def stats(hist, per_hour=4):
    h, l, c, v = [x[2] for x in hist], [x[3] for x in hist], [x[4] for x in hist], [x[5] for x in hist]
    atr = I.atr(h, l, c, 14)[-1] or 1e-9
    e50, e200 = I.ema(c, 50)[-1] or c[-1], I.ema(c, 200)[-1] or c[-1]
    adx = I.adx(h, l, c, 14)
    rng_lo, rng_hi = min(l[-96:]), max(h[-96:])
    pc = lambda n: (c[-1] / c[-1 - n] - 1) * 100
    last12 = [round((c[-k] / c[-k - 1] - 1) * 100, 2) for k in range(12, 0, -1)]
    return dict(px=c[-1], atr_pct=atr / c[-1] * 100, r1h=pc(per_hour), r4h=pc(4 * per_hour), r24h=pc(min(96, len(c) - 1)), d50=(c[-1] / e50 - 1) * 100,
                d200=(c[-1] / e200 - 1) * 100, rsi=I.rsi(c, 14)[-1] or 50, adx=adx[-1] or 0, adx_prev=adx[-4] or 0, rng=(c[-1] - rng_lo) / (rng_hi - rng_lo) * 100 if rng_hi > rng_lo else 50,
                vol=v[-1] / (sum(v[-21:-1]) / 20) if sum(v[-21:-1]) else 1.0, last12=last12, atr=atr)


def dossier(store, inst, hist, btc, poss, equity, reasons, tf):
    s = stats(hist)
    lines = ["%s %s | trigger: %s" % (inst["symbol"], tf, "; ".join(reasons)),
             "px %.6g ATR %.2f%% | ret 1h %+.2f%% 4h %+.2f%% 24h %+.2f%% | vs EMA50 %+.2f%% EMA200 %+.2f%% | RSI %.0f | ADX %.0f (was %.0f) | 24h range pos %.0f%% | vol %.1fx" % (
                 s["px"], s["atr_pct"], s["r1h"], s["r4h"], s["r24h"], s["d50"], s["d200"], s["rsi"], s["adx"], s["adx_prev"], s["rng"], s["vol"]),
             "last 12 bars %% chg: %s" % s["last12"]]
    if btc is not None and len(btc) > 100 and not inst["symbol"].startswith("BTC"):
        bc = [x[4] for x in btc]
        lines.append("BTC 1h %+.2f%% 4h %+.2f%%" % ((bc[-1] / bc[-5] - 1) * 100, (bc[-1] / bc[-17] - 1) * 100))
    if inst["market"] == "futures":
        try:
            lines.append(analyst.render_derivs(analyst.derivs(inst["symbol"])))
        except Exception:
            lines.append("derivs unavailable")
    risk = sum(abs(p.get("mark", p["entry"]) - p["stop"]) * p["qty"] for p in poss.values())
    lines.append("book: %d open, risk in play %.1f%% of equity | UTC hour %d" % (len(poss), risk / equity * 100 if equity else 0, time.gmtime().tm_hour))
    mine = [t for t in store.trades(80) if t.get("source") == "day"]
    if mine:
        w = sum(1 for t in mine if t["pnl"] > 0)
        sym = [t for t in mine if t["symbol"] == inst["symbol"]][:3]
        lines.append("my day trades: %d, %d%% won, net %+.1f%s" % (len(mine), w * 100 // len(mine), sum(t["pnl"] for t in mine), (" | here: " + ", ".join("%+.1f" % t["pnl"] for t in sym)) if sym else ""))
    return "\n".join(lines)


def parse(text):
    a = re.search(r'"action"\s*:\s*"(long|short|flat)"', text or "")
    if not a:
        return None
    num = lambda k, d: float((re.search(r'"%s"\s*:\s*(\d+(?:\.\d+)?)' % k, text) or [0, d])[1])
    why = re.search(r'"why"\s*:\s*"([^"]*)', text)
    return dict(action=a.group(1), conf=num("conf", 0), stop_atr=min(3.0, max(0.6, num("stop_atr", 1.5))), target_atr=min(5.0, max(0.8, num("target_atr", 2.0))), why=(why.group(1) if why else "")[:140])


_memo = {}


def decide(store, inst, hist, btc, poss, equity, fetch, reasons, tf):
    """-> dict(action, conf, stop_atr, target_atr, why, usd, tokens, ms, source). Never raises; any failure means action='flat'."""
    cfg = store.cfg["ai"]
    out = dict(action="flat", conf=0, stop_atr=1.5, target_atr=2.0, why="", usd=0.0, tokens=0, ms=0, source="")
    key = (inst["id"], hist[-1][0])
    if key in _memo:
        return dict(_memo[key], source="memo")
    if time.time() < analyst._breaker["until"]:
        return dict(out, why="AI paused after errors", source="breaker")
    if cfg["provider"] == "claude_cli" or not llm.configured(cfg):
        return dict(out, why="AI is not connected - day trading needs one", source="nokey")
    ok, why = analyst.budget_ok(store)
    if not ok:
        return dict(out, why=why, source="budget")
    t0 = time.time()
    try:
        doss = dossier(store, inst, hist, btc, poss, equity, reasons, tf)
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": doss}]
        body = llm.build_body(cfg, msgs, tools=analyst.TOOLS, max_tokens=160)
        rounds = int(cfg.get("max_tool_rounds", 2))
        tin = tout = 0
        for rnd in range(rounds + 1):
            if rnd == rounds:
                body.pop("tools", None)
            r = analyst._post(cfg, body, cfg.get("timeout", 12))
            hit, miss, o, usd = analyst.record(store, r.get("usage", {}), cfg["model"])
            tin, tout, out["usd"] = tin + hit + miss, tout + o, out["usd"] + usd
            msg = r["choices"][0]["message"]
            if msg.get("tool_calls"):
                msgs.append({k: msg[k] for k in ("role", "content", "tool_calls") if k in msg})
                for tc in msg["tool_calls"]:
                    try:
                        res = analyst.run_tool(tc["function"]["name"], json.loads(tc["function"].get("arguments") or "{}"), store, fetch)
                    except Exception as e:
                        res = "tool error: %s" % str(e)[:60]
                    msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": res})
                continue
            d = parse(msg.get("content"))
            if d is None:
                raise RuntimeError("unreadable reply")
            if d["action"] != "flat" and d["target_atr"] < 1.2 * d["stop_atr"]:
                d.update(action="flat", why="reward/risk too low: " + d["why"])
            out.update(d, tokens=tin + tout, ms=int((time.time() - t0) * 1000), source="api")
            store.ai_log_add(inst=inst["id"] if inst["id"].startswith("day:") else "day:" + inst["id"], prompt=doss, reply="%s %d%% %s" % (d["action"], d["conf"], d["why"]), take=int(d["action"] != "flat"), tokens_in=tin, tokens_out=tout, ms=out["ms"])
            analyst._breaker["fails"] = 0
            _memo[key] = out
            return out
        raise RuntimeError("no answer after tool rounds")
    except Exception as e:
        analyst._breaker["fails"] += 1
        if analyst._breaker["fails"] >= 3:
            analyst._breaker.update(until=time.time() + 600, fails=0)
        return dict(out, why="AI unavailable: " + llm.friendly(e)[:70], source="error")
