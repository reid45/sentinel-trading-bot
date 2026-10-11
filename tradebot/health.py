"""Health checks: what is wrong, in plain English, and what to do about it. Shown in the app (the Health chip) and by `sentinel.py doctor`."""
import os, shutil, time
from . import analyst, data, llm

LEVELS = {"ok": 0, "info": 0, "warn": 1, "bad": 2}


def _c(level, title, detail="", fix=""):
    return dict(level=level, title=title, detail=detail, fix=fix)


def checks(store, engine, deploy=None):
    """deploy = dict(auth=bool, public=bool, host=str, https=bool) describing how the server is exposed."""
    out = []
    cfg, now = store.cfg, time.time()
    snap = engine.snapshot()

    # --- engine
    if engine.halted():
        out.append(_c("bad", "Trading is halted", "The kill switch or the drawdown limit stopped the bot.", "Review what happened, then press Resume in Settings."))
    elif not engine.running:
        out.append(_c("warn", "Trading is paused", "The bot is not looking for trades.", "Press Run at the top."))
    else:
        age = (snap["now"] - snap["last_cycle"]) / 1000 if snap["last_cycle"] else 9e9
        if age > 120:
            out.append(_c("bad", "The trading loop looks stuck", "The last check finished %d seconds ago." % min(age, 99999), "Restart Sentinel. If it repeats, the data feed is probably timing out."))
        else:
            out.append(_c("ok", "Trading loop running", "Last check %d seconds ago across %d markets." % (age, len(cfg["instruments"]))))
    if snap.get("daily_block"):
        out.append(_c("warn", "Daily loss limit reached", "New trades are blocked until 00:00 UTC.", "Nothing to do; it resumes by itself."))

    # --- market data
    bad = {k: v["error"] for k, v in snap["status"].items() if v.get("error")}
    ban = data._ban["until"] - now
    if ban > 0:
        out.append(_c("warn", "Binance asked us to slow down", "The data feed is paused for %d more seconds (Bybit is used meanwhile)." % ban, "Nothing to do; it recovers on its own."))
    src = data.LAST_SRC.get("futures")
    if src and src != "Binance":
        out.append(_c("warn", "Using a backup price feed", "Crypto prices are coming from: %s." % src, "Fine for now. If it stays like this, check that Binance is reachable from this server (`sentinel.py doctor`)."))
    dead = [k.split(":")[1] for k, e in bad.items() if "no volume" in e]
    if dead:
        out.append(_c("warn", "%d market(s) not trading" % len(dead), ", ".join(dead[:8]) + " report no volume, so the bot ignores them.", "Remove them in Markets, or ignore."))
    other = {k: e for k, e in bad.items() if "no volume" not in e}
    if other:
        k, e = next(iter(other.items()))
        out.append(_c("warn" if len(other) < max(3, len(snap["status"]) // 4) else "bad", "%d market(s) have a data problem" % len(other), "%s: %s" % (k.split(":")[1], e), "Usually temporary. If it persists: `sentinel.py doctor`."))
    rate = data.calls_last_minute()
    if rate > 900:
        out.append(_c("warn", "Many data requests (%d in the last minute)" % rate, "Binance allows about 2,400 weight per minute; staying far below it avoids a temporary ban.", "Watch fewer markets, or switch the scalper off."))
    if not bad and ban <= 0:
        out.append(_c("ok", "Market data healthy", "All %d markets are updating (%d data requests in the last minute)." % (len(snap["status"]), rate)))

    # --- positions
    if snap["drawdown_pct"] > cfg["risk"]["max_drawdown_halt_pct"] * 0.6:
        out.append(_c("warn", "Drawdown is %.1f%%" % snap["drawdown_pct"], "The bot halts at %.0f%%." % cfg["risk"]["max_drawdown_halt_pct"], "Consider lowering risk per trade."))

    # --- AI
    ai = cfg["ai"]
    uses_ai = ai.get("mode") in ("veto", "advise") or cfg["day"].get("enabled")
    if uses_ai and not llm.configured(ai):
        out.append(_c("warn", "AI is switched on but not connected", "AI features are doing nothing.", "Click the AI chip at the top and add a key (or set the AI mode to Off)."))
    elif llm.configured(ai):
        ok, why = analyst.budget_ok(store)
        sp = analyst.spend(store)
        out.append(_c("ok" if ok else "warn", "AI connected" if ok else "AI spending cap reached", "%s: %d calls and $%.3f today." % (ai.get("provider"), sp["calls_day"], sp["usd_day"]) + ("" if ok else " " + why), "" if ok else "Raise the cap on the AI page if you want it to continue."))
        if time.time() < analyst._breaker["until"]:
            out.append(_c("warn", "AI paused after errors", "Several AI calls failed in a row; it retries automatically.", "Check the key and balance on the AI page."))

    # --- settings that run against the research
    fast = sorted({i["tf"] for i in cfg["instruments"] if i["market"] == "futures" and i["tf"] in ("1m", "5m", "15m", "1h")})
    if fast:
        out.append(_c("warn", "The swing bot is running on %s candles" % "/".join(fast),
                      "In our tests every strategy lost money after fees at this speed; only 4-hour candles were profitable. Faster means more trades and more fees, not more profit.",
                      "Strategies page, Balanced or Careful preset: it restores 4-hour candles and a tested risk level (open swing trades are closed first)."))
    rk = cfg["risk"]["per_trade_pct"]
    if rk >= 1.0:
        out.append(_c("warn", "Risk per trade is %.1f%%" % rk,
                      "Back-test of this exact system over 3.4 years: 0.5%% risk made +54%% with a worst dip of 27%%; 1%% made +88%% with a 49%% dip; 2%% made only +7%% with a 79%% dip "
                      "(the leverage cap starts skipping trades and the dips swallow the gains). More risk is not more profit.",
                      "Account chip at the top: Balanced (0.5%) is the best trade-off, or apply a preset on the Strategies page."))

    # --- rules and activity
    if cfg["rules"]["hours"]["enabled"]:
        out.append(_c("info", "Trading hours are on", "New trades only between %02d:00 and %02d:00 UTC." % (cfg["rules"]["hours"]["start"], cfg["rules"]["hours"]["end"] % 24)))
    mix = [i for i in cfg["instruments"] if i.get("strategy") == "trendmix" and i.get("enabled", True)]
    if mix and engine.running:
        out.append(_c("info", "Trend mix watching %d coins" % len(mix), "It trades when a 4-hour candle closes beyond its recent range, so quiet stretches without trades are normal.", ""))
    if not (cfg["notify"].get("url") or cfg["notify"].get("telegram_token")):
        out.append(_c("info", "No alerts set up", "You only see trades when the dashboard is open.", "Settings, Alerts: Discord, Slack or Telegram."))

    # --- host and security
    d = deploy or {}
    if d.get("public") and not d.get("https"):
        out.append(_c("warn", "This page was not loaded over https", "Passwords would cross the internet unencrypted.", "Serve it through Caddy or nginx with a TLS certificate (docs/DEPLOY.md)."))
    if d.get("exposed") and not d.get("auth"):
        out.append(_c("bad", "Listening on the network without a login", "Anyone who can reach this port can control the bot.", "Set SENTINEL_AUTH=1 and create the owner account, or listen on 127.0.0.1 only."))
    if cfg["mode"] == "live" and not d.get("auth") and d.get("public"):
        out.append(_c("bad", "Live trading on a public address needs a login", "", "Turn on sign-in first."))
    try:
        free = shutil.disk_usage(data.DATA_DIR).free / 1e6
        if free < 300:
            out.append(_c("warn", "Low disk space", "%d MB free where Sentinel stores its data." % free, "Free some space; the database stops growing safely only if the disk has room."))
    except OSError:
        pass
    if not os.access(data.DATA_DIR, os.W_OK):
        out.append(_c("bad", "Data folder is not writable", data.DATA_DIR, "Fix permissions, otherwise nothing is saved."))
    return out


def summary(items):
    worst = max([LEVELS[i["level"]] for i in items] or [0])
    n = sum(1 for i in items if LEVELS[i["level"]] >= 1)
    return dict(level=["ok", "warn", "bad"][worst], issues=n)
