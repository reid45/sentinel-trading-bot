"""Chat with the trader. Read-only: it can look things up and explain, never place or change trades.
Answers use tools so numbers are never guessed; every tool step is returned so the UI can show what it looked at."""
import json, time
from . import analyst, context, llm

SYSTEM = ("You are Sentinel, the trader inside a trading app, chatting with its owner. Answer questions about the account, open positions, markets and your own recent "
          "decisions. Always use tools for facts and numbers; never guess. You cannot place or change trades from chat. Be concise (under 110 words), plain English, "
          "and honest about uncertainty and risk. If asked for financial advice, explain what the bot does and its measured limits instead.")
TOOLS = [
    {"type": "function", "function": {"name": "account", "description": "Account value, profit, today's P&L, drawdown, win rate, safety status.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "positions", "description": "Open positions with entry, price, stop, target and profit.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "thoughts", "description": "The trader's most recent reasoning and actions (newest first).", "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "market", "description": "Current price, trend, volatility and status of one watched market.", "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}}},
] + [t for t in analyst.TOOLS if t["function"]["name"] in ("journal", "derivs", "candles", "levels", "mtf", "flow", "edge")]


def _tool(name, args, store, engine):
    if name == "account":
        s = engine.snapshot()
        return json.dumps({k: (round(s[k], 2) if isinstance(s[k], float) else s[k]) for k in ("equity", "start_equity", "total_return_pct", "day_pnl", "day_pnl_pct", "drawdown_pct", "trades", "win_rate", "running", "halted", "daily_block", "mode")})
    if name == "positions":
        ps = engine.snapshot()["positions"]
        return json.dumps([dict(symbol=p["symbol"], side="long" if p["side"] > 0 else "short", entry=round(p["entry"], 6), price=round(p.get("mark", p["entry"]), 6), stop=round(p["stop"], 6),
                                target=p.get("tp"), profit=round(p["upnl"], 2), source=p.get("source")) for p in ps]) if ps else "no open positions"
    if name == "thoughts":
        rows = store.thoughts(max(3, min(int(args.get("n", 10)), 25)))
        return "\n".join("%s %s: %s" % (time.strftime("%H:%M", time.localtime(r["t"] / 1000)), r["symbol"], r["text"]) for r in rows) or "no thoughts yet"
    if name == "market":
        inst = {i["symbol"].upper(): i for i in store.cfg["instruments"]}.get(str(args.get("symbol", "")).upper())
        if not inst:
            return "not on the watchlist"
        st = engine.status.get(inst["id"], {})
        hist = engine.fetch(inst["market"], inst["symbol"], inst["tf"], 300)
        f = context.features(hist, 1)
        return "%s price %s | 24h %s%% | trend vs EMA200 %+.1f%% | ADX %d | ATR %.2f%% | tf %s | bot %s | signal %s" % (
            inst["symbol"], st.get("price"), round(st.get("chg24", 0), 2), f["d200"], f["adx"], f["atr_pct"], inst["tf"], "trading" if inst["enabled"] else "watching only", st.get("signal"))
    return analyst.run_tool(name, args, store, engine.fetch)


def ask(store, engine, history):
    """history: [{role:'user'|'assistant', content}] (last user message is the question). -> dict(answer, steps, usd)"""
    cfg = store.cfg["ai"]
    out = dict(answer="", steps=[], usd=0.0)
    if cfg["provider"] == "claude_cli" or not llm.configured(cfg):
        return dict(out, answer="I need an AI connection to chat. Click the AI chip at the top to connect one (it takes a minute).", error="nokey")
    ok, why = analyst.budget_ok(store)
    if not ok:
        return dict(out, answer="I've hit the AI spending cap (%s). You can raise it on the AI page." % why, error="budget")
    msgs = [{"role": "system", "content": SYSTEM}] + [{"role": m["role"], "content": str(m["content"])[:800]} for m in history[-8:] if m.get("role") in ("user", "assistant")]
    tools = TOOLS
    if not llm.uses_tools(cfg):      # small local models: pre-load facts instead of tool calls
        ctx = "\n".join([_tool("account", {}, store, engine), _tool("positions", {}, store, engine), _tool("thoughts", {"n": 8}, store, engine)])
        msgs[-1]["content"] += "\n\n[current facts]\n" + ctx
        tools = None
    body = llm.build_body(cfg, msgs, tools=tools, max_tokens=380, temperature=0.3)
    try:
        for rnd in range(5):
            if rnd == 4:
                body.pop("tools", None)
            r = analyst._post(cfg, body, 30)
            usd = analyst.record(store, r.get("usage", {}), cfg["model"])[3]
            out["usd"] += usd
            msg = r["choices"][0]["message"]
            if msg.get("tool_calls"):
                msgs.append({k: msg[k] for k in ("role", "content", "tool_calls") if k in msg})
                for tc in msg["tool_calls"]:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                        res = _tool(name, args, store, engine)
                    except Exception as e:
                        args, res = {}, "tool error: %s" % str(e)[:60]
                    out["steps"].append(dict(tool=name, args=args, result=res[:160]))
                    msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": res[:1500]})
                continue
            out["answer"] = (msg.get("content") or "").strip() or "I couldn't put together an answer. Try rephrasing."
            return out
    except Exception as e:
        return dict(out, answer="I couldn't reach the AI: " + llm.friendly(e), error="api")
    return dict(out, answer="I looked things up but ran out of steps. Try a more specific question.")
