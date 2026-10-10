"""Entry rules and protections applied to every new position, before any order is placed.
Inspired by freqtrade's "protections" (CooldownPeriod, StoplossGuard, MaxDrawdown) plus plain trading-hours / direction / budget switches.
Every refusal returns a plain-English reason that the Trader page shows, so the bot never silently does nothing."""
import time


def _day_start_ms(now_ms):
    return now_ms - now_ms % 86400000


def entry_allowed(store, inst, side, risk_usd, source, equity, poss, now_ms=None):
    """-> (ok, message). `risk_usd` is the money this entry puts at risk (loss at its stop, fees included); None = pre-check before sizing (also side=0)."""
    r = store.cfg.get("rules") or {}
    now_ms = now_ms or int(time.time() * 1000)
    t = time.gmtime(now_ms / 1000)

    h = r.get("hours") or {}
    if h.get("enabled"):
        start, end = int(h.get("start", 0)), int(h.get("end", 24))
        inside = (start <= t.tm_hour < end) if start <= end else (t.tm_hour >= start or t.tm_hour < end)
        if not inside or t.tm_wday not in (h.get("days") or range(7)):
            return False, "outside your trading hours (%02d:00-%02d:00 UTC)" % (start, end % 24 if end != 24 else 0)

    d = r.get("direction", "both")
    if side and ((d == "long" and side == -1) or (d == "short" and side == 1)):
        return False, "your rules allow %s trades only" % d

    trades = store.trades(80)
    open_today = sum(1 for p in poss.values() if p.get("t_open", 0) >= _day_start_ms(now_ms))
    cap = int(r.get("max_trades_day") or 0)
    if cap and open_today + sum(1 for x in trades if x["t_open"] >= _day_start_ms(now_ms)) >= cap:
        return False, "reached today's limit of %d trades" % cap

    cool = float(r.get("cooldown_min") or 0) * 60000
    if cool:
        last = next((x for x in trades if x["symbol"] == inst["symbol"]), None)
        if last and now_ms - last["t_close"] < cool:
            return False, "cooling down on %s (%d more minutes)" % (inst["symbol"], int((cool - (now_ms - last["t_close"])) / 60000) + 1)

    n, mins = int(r.get("pause_after_stops") or 0), float(r.get("pause_minutes") or 60)
    if n:
        stops = [x for x in trades if x["reason"] == "stop" and x["pnl"] < 0 and now_ms - x["t_close"] < 3600000]
        if len(stops) >= n and now_ms - max(x["t_close"] for x in stops) < mins * 60000:
            return False, "paused: %d stop-outs within an hour (back at %s UTC)" % (len(stops), time.strftime("%H:%M", time.gmtime((max(x["t_close"] for x in stops) + mins * 60000) / 1000)))

    cap = float(r.get("max_open_risk_pct") or 0)
    if cap and source == "day" and risk_usd is not None:
        live = sum(abs(p.get("mark", p["entry"]) - p["stop"]) * p["qty"] for p in poss.values() if p.get("source") == "day")
        if equity > 0 and (live + risk_usd) / equity * 100 > cap:
            return False, "total day-trade risk would be %.1f%% of the account (your cap is %.1f%%)" % ((live + risk_usd) / equity * 100, cap)
    return True, ""
