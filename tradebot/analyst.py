"""AI signal analyst: one cheap, cache-friendly, tool-capable DeepSeek call per *actionable* signal.

Token/cost design (per DeepSeek docs): context caching is automatic on identical prompt PREFIXES, so SYSTEM + TOOLS are static bytes
and the per-signal dossier goes last. Non-thinking mode, short JSON answer, max 2 tool rounds, hard daily/total budget,
per-(market,bar,side) memo so a retry never re-bills, circuit breaker on repeated failures. Any failure fails open."""
import json, time, threading, urllib.request, urllib.error
from . import context, data

SYSTEM = (
    "You are Sentinel's signal analyst for a mechanical 4h trend-following breakout bot on crypto perpetuals. "
    "The signal already passed the strategy and risk limits; judge only whether conditions are clearly hostile. "
    "Base rate: ~38% of signals pay, winners are large. Typical losers: false breakouts in low-ADX chop, exhausted or crowded moves "
    "(extreme funding, OI spike with price stalling), and entries against BTC's trend. "
    "The dossier is complete for most signals; call a tool only if a specific fact is missing (max 2 calls). "
    'Reply with JSON only: {"p":<0-100 follow-through probability>,"flag":"none|chop|exhausted|crowded|btc_headwind|other","why":"<=10 words"}'
)
TOOLS = [
    {"type": "function", "function": {"name": "candles", "description": "Recent closes of a configured market, normalised to 100 at the first bar.",
                                       "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}, "tf": {"type": "string", "enum": ["1h", "4h", "1d"]},
                                                                                        "n": {"type": "integer"}}, "required": ["symbol", "tf"]}}},
    {"type": "function", "function": {"name": "derivs", "description": "Funding history, open-interest change and long/short ratio for a crypto perp.",
                                       "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}},
    {"type": "function", "function": {"name": "journal", "description": "The bot's last closed trades on a market.",
                                       "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}},
]
# USD per 1M tokens: (off-peak, peak) per DeepSeek pricing page. Unknown models are costed as flash-at-peak (conservative).
PRICES = {"deepseek-flash": dict(hit=(0.003, 0.006), miss=(0.15, 0.30), out=(0.6, 1.2)),
          "deepseek-v4-pro": dict(hit=(0.022, 0.044), miss=(0.66, 1.32), out=(1.98, 3.96))}
_lock = threading.Lock()
_memo, _deriv_cache, _breaker = {}, {}, {"fails": 0, "until": 0.0}


def is_peak(t=None):
    t = time.gmtime(t or time.time())
    return t.tm_wday < 5 and (1 <= t.tm_hour < 4 or 6 <= t.tm_hour < 10)


def cost_usd(model, hit, miss, out, t=None):
    p = PRICES.get(model, PRICES["deepseek-flash"])
    i = 1 if is_peak(t) else 0
    return (hit * p["hit"][i] + miss * p["miss"][i] + out * p["out"][i]) / 1e6


# ------------------------------------------------------------------ budget
def spend(store):
    s = store.get("ai_spend") or {}
    today = time.strftime("%Y-%m-%d", time.gmtime())
    if s.get("day") != today:
        s = dict(s, day=today, calls_day=0, usd_day=0.0)
    return dict(dict(calls_day=0, usd_day=0.0, calls=0, usd=0.0, tokens=0, hit=0, miss=0), **s)


def budget_ok(store):
    b, s = store.cfg["ai"].get("budget", {}), spend(store)
    if s["calls_day"] >= b.get("max_calls_day", 120):
        return False, "daily AI call budget used (%d)" % s["calls_day"]
    if s["usd_day"] >= b.get("max_usd_day", 0.25):
        return False, "daily AI $ budget used ($%.3f)" % s["usd_day"]
    if s["usd"] >= b.get("max_usd_total", 3.0):
        return False, "total AI $ budget used ($%.3f)" % s["usd"]
    return True, ""


def record(store, usage, model):
    hit, miss, out = usage.get("prompt_cache_hit_tokens", 0), usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0)), usage.get("completion_tokens", 0)
    usd = cost_usd(model, hit, miss, out)
    with _lock:
        s = spend(store)
        s.update(calls_day=s["calls_day"] + 1, usd_day=s["usd_day"] + usd, calls=s["calls"] + 1, usd=s["usd"] + usd,
                 tokens=s["tokens"] + hit + miss + out, hit=s["hit"] + hit, miss=s["miss"] + miss)
        store.put("ai_spend", s)
    return hit, miss, out, usd


# ------------------------------------------------------------------ tools / data
def fapi(path, **q):
    from urllib.parse import urlencode
    return data._get("https://fapi.binance.com" + path + "?" + urlencode(q), timeout=6)


def derivs(symbol):
    c = _deriv_cache.get(symbol)
    if c and time.time() - c[0] < 120:
        return c[1]
    pi = fapi("/fapi/v1/premiumIndex", symbol=symbol)
    fr = [float(x["fundingRate"]) * 100 for x in fapi("/fapi/v1/fundingRate", symbol=symbol, limit=8)]
    oi = [float(x["sumOpenInterest"]) for x in fapi("/futures/data/openInterestHist", symbol=symbol, period="4h", limit=7)]
    ls = fapi("/futures/data/globalLongShortAccountRatio", symbol=symbol, period="4h", limit=1)
    mark, idx = float(pi["markPrice"]), float(pi["indexPrice"])
    out = dict(funding=float(pi["lastFundingRate"]) * 100, funding_avg8=sum(fr) / len(fr) if fr else 0.0, funding_max8=max(fr, key=abs) if fr else 0.0,
               oi_24h=(oi[-1] / oi[0] - 1) * 100 if len(oi) > 1 and oi[0] else 0.0, ls=float(ls[0]["longShortRatio"]) if ls else None,
               basis=(mark / idx - 1) * 100 if idx else 0.0)
    _deriv_cache[symbol] = (time.time(), out)
    return out


def render_derivs(d):
    return "funding %+.3f%%/8h (avg8 %+.3f, extreme %+.3f) | OI 24h %+.1f%% | L/S %s | basis %+.2f%%" % (
        d["funding"], d["funding_avg8"], d["funding_max8"], d["oi_24h"], "%.2f" % d["ls"] if d["ls"] else "n/a", d["basis"])


def run_tool(name, args, store, fetch):
    inst = {i["symbol"].upper(): i for i in store.cfg["instruments"]}.get(str(args.get("symbol", "")).upper())
    if not inst:
        return "unknown symbol"
    if name == "derivs":
        return render_derivs(derivs(inst["symbol"])) if inst["market"] == "futures" else "n/a (not a crypto perp)"
    if name == "journal":
        t = [x for x in store.trades(200) if x["symbol"] == inst["symbol"]][:5]
        return ", ".join("%s %+.1f(%s)" % ("L" if x["side"] > 0 else "S", x["pnl"], x["reason"]) for x in t) or "no closed trades yet"
    if name == "candles":
        tf, n = args.get("tf", "4h"), max(5, min(int(args.get("n", 24)), 48))
        c = fetch(inst["market"], inst["symbol"], tf, n + 2)[-n:]
        base = c[0][4]
        return json.dumps({"closes": [round(x[4] / base * 100, 1) for x in c], "hi": round(max(x[2] for x in c) / base * 100, 1), "lo": round(min(x[3] for x in c) / base * 100, 1)})
    return "unknown tool"


def dossier(store, inst, side, f, poss, equity):
    """The compact per-signal facts the model needs, so most signals need exactly ONE request."""
    parts = [context.render(inst["symbol"], side, f)]
    if inst["market"] == "futures":
        try:
            parts.append(render_derivs(derivs(inst["symbol"])))
        except Exception:
            parts.append("derivs unavailable")
    same = sum(1 for p in poss.values() if p["side"] == side)
    risk = sum(abs(p.get("mark", p["entry"]) - p["stop"]) * p["qty"] for p in poss.values())
    btc = next((p for p in poss.values() if p["symbol"].startswith("BTC")), None)
    parts.append("book: %d open (%d same dir), risk in play %.1f%% of equity, BTC %s" % (
        len(poss), same, risk / equity * 100 if equity else 0, "flat" if not btc else ("long" if btc["side"] > 0 else "short")))
    rec = [x for x in store.trades(60) if x["symbol"] == inst["symbol"]][:4]
    if rec:
        parts.append("recent here: " + ", ".join("%+.0f" % x["pnl"] for x in rec))
    return "\n".join(parts)


# ------------------------------------------------------------------ the call
def _post(cfg, body, timeout):
    req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/chat/completions", method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["api_key"]}, data=json.dumps(body, separators=(",", ":")).encode())
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def parse(text):
    import re
    m = re.search(r'"p"\s*:\s*(\d+(?:\.\d+)?)', text or "")
    if not m:
        return None
    flag = re.search(r'"flag"\s*:\s*"([a-z_]+)"', text)
    why = re.search(r'"why"\s*:\s*"([^"]*)', text)
    return dict(p=float(m.group(1)), flag=flag.group(1) if flag else "none", why=(why.group(1) if why else "")[:80])


def assess(store, inst, side, hist, btc, poss, equity, fetch):
    """Returns dict(take, p, flag, why, note, usd, tokens, ms, source). Never raises. Fails open."""
    cfg = store.cfg["ai"]
    out = dict(take=True, p=None, flag="none", why="", note="", usd=0.0, tokens=0, ms=0, source="")
    if cfg["mode"] not in ("veto", "advise"):
        return dict(out, note="AI off")
    key = (inst["id"], hist[-1][0], side)
    if key in _memo:
        return dict(_memo[key], source="memo")
    if time.time() < _breaker["until"]:
        return dict(out, note="AI paused after errors", source="breaker")
    if cfg["provider"] != "claude_cli" and not cfg.get("api_key"):
        return dict(out, note="AI has no key", source="nokey")
    ok, why = budget_ok(store)
    if not ok:
        return dict(out, note=why, source="budget")
    t0 = time.time()
    try:
        f = context.features(hist, side, btc=btc)
        doss = dossier(store, inst, side, f, poss, equity)
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": doss}]
        body = dict(model=cfg["model"], messages=msgs, max_tokens=140, temperature=0)
        if "deepseek" in cfg["base_url"]:
            body["thinking"] = {"type": "disabled"}
        if cfg.get("tools", True):
            body["tools"] = TOOLS
        tin = tout = 0
        for rnd in range(int(cfg.get("max_tool_rounds", 2)) + 1):
            if rnd == int(cfg.get("max_tool_rounds", 2)):
                body.pop("tools", None)               # force a final answer
            r = _post(cfg, body, cfg.get("timeout", 12))
            hit, miss, o, usd = record(store, r.get("usage", {}), cfg["model"])
            tin, tout, out["usd"] = tin + hit + miss, tout + o, out["usd"] + usd
            msg = r["choices"][0]["message"]
            if msg.get("tool_calls"):
                msgs.append({k: msg[k] for k in ("role", "content", "tool_calls") if k in msg})
                for tc in msg["tool_calls"]:
                    try:
                        res = run_tool(tc["function"]["name"], json.loads(tc["function"].get("arguments") or "{}"), store, fetch)
                    except Exception as e:
                        res = "tool error: %s" % str(e)[:60]
                    msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": res})
                continue
            a = parse(msg.get("content"))
            if a is None:
                raise RuntimeError("unreadable reply %r" % (msg.get("content") or "")[:50])
            out.update(a, tokens=tin + tout, ms=int((time.time() - t0) * 1000), source="api")
            out["take"] = True if cfg["mode"] == "advise" else a["p"] >= cfg.get("min_p", 0)
            out["note"] = "AI %d%% %s: %s" % (a["p"], a["flag"], a["why"])
            _breaker["fails"] = 0
            store.ai_log_add(inst=inst["id"], prompt=doss, reply="p=%d %s %s" % (a["p"], a["flag"], a["why"]), take=int(out["take"]),
                             tokens_in=tin, tokens_out=tout, ms=out["ms"])
            _memo[key] = out
            return out
        raise RuntimeError("no answer after tool rounds")
    except Exception as e:
        _breaker["fails"] += 1
        if _breaker["fails"] >= 3:
            _breaker.update(until=time.time() + 600, fails=0)
        store.ai_log_add(inst=inst["id"], prompt="", reply="ERROR " + str(e)[:160], take=1, ms=int((time.time() - t0) * 1000))
        return dict(out, note="AI unavailable (%s) - proceeding" % str(e)[:50], source="error", ms=int((time.time() - t0) * 1000))


def reset_runtime():
    """Test helper."""
    _memo.clear(); _deriv_cache.clear(); _breaker.update(fails=0, until=0.0)
