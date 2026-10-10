"""SQLite persistence + JSON config (secrets stay on this machine in data/config.json)."""
import os, json, sqlite3, threading, time, copy
from . import catalog, notify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("TRADEBOT_DATA") or os.path.join(ROOT, "data")

def _mk(market, sym, on=True):
    row = catalog.lookup(market, sym) or dict(market=market, symbol=sym, name=sym, cat="crypto" if market == "futures" else market)
    return catalog.make_instrument(row, "4h", on)


DEFAULTS = {
    "mode": "paper",                      # paper | live
    "start_equity": 10000.0,
    "poll_seconds": 20,
    "risk": {"per_trade_pct": 0.3, "max_positions": 15, "max_leverage_per_pos": 3.0, "max_total_leverage": 3.0,
             "daily_loss_limit_pct": 3.0, "max_drawdown_halt_pct": 20.0},
    "instruments": [_mk("futures", s) for s in catalog.UNIVERSE] +
                   [_mk("forex", s) for s in ("EURUSD=X", "GBPUSD=X", "USDJPY=X")] +
                   [_mk("stock", s, on=False) for s in catalog.FEATURED_STOCKS],        # big companies are listed watch-only; one tap lets the bot trade them
    "ai": {"mode": "advise", "provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
           "api_key": "", "timeout": 12, "min_p": 0, "tools": True, "max_tool_rounds": 2,
           "budget": {"max_calls_day": 120, "max_usd_day": 0.25, "max_usd_total": 3.0}},
    "broker": {"exchange": "bybit", "bybit_env": "demo", "bybit_key": "", "bybit_secret": "",
               "binance_key": "", "binance_secret": "", "binance_testnet": True},
    "webhook_secret": "",
    "ui": {"onboarded": False, "tour_done": False, "brand": "Sentinel", "theme": "dark", "accent": "#7c9cff", "density": "comfortable", "font": 100, "radius": 16,
           "palette": "classic", "motion": True, "landing": "portfolio", "clock": "24", "currency": "USD",
           "home": {"insight": True, "kpis": True, "safety": True, "open": True, "markets": True}},
    "day": {"enabled": False, "tf": "15m", "markets": 8, "min_conf": 50, "max_hold_bars": 12},
    "scalp": {"enabled": False, "markets": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"], "maker_bps": 2.0, "taker_bps": 5.0, "slip_bps": 1.0,
              "k": 2.0, "rsi": 20, "off": 0.3, "wait": 3, "tgt": 0.6, "stop": 2.0, "hold": 10,
              "risk_pct": 0.1, "max_trades_day": 300, "daily_loss_pct": 1.0, "auto_pause": True, "auto_pause_trades": 40},
    "smart": {"enabled": False, "crypto": True, "stocks": False, "tf": "15m", "markets": 8, "min_score": 5, "max_trades_day": 3, "rr_min": 2.0, "risk_pct": 0.5, "use_ai": True,
              "min_dollar_vol_m": 100.0, "max_spread_bps": 4.0, "max_hold_bars": 16, "stock_symbols": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA"], "stock_min_dollar_vol_m": 500.0},
    "rules": {"hours": {"enabled": False, "start": 8, "end": 22, "days": [0, 1, 2, 3, 4, 5, 6]},      # UTC hours; trade only inside this window
              "direction": "both",                                  # both | long | short
              "max_trades_day": 0,                                  # 0 = no limit
              "cooldown_min": 15,                                   # wait after closing a market before re-entering it
              "pause_after_stops": 3, "pause_minutes": 60,          # freqtrade-style StoplossGuard: N stop-outs within an hour pause new entries
              "max_open_risk_pct": 4.0,                             # total money-at-risk across open day trades, % of equity
              "min_rr": 1.2},
    "notify": {"kind": "discord", "url": "", "telegram_token": "", "telegram_chat": "", "on_open": True, "on_close": True, "on_alert": True},
}


SECRETS = (("ai", "api_key"), ("broker", "binance_key"), ("broker", "binance_secret"), ("broker", "bybit_key"), ("broker", "bybit_secret"),
           ("notify", "url"), ("notify", "telegram_token"))


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Store:
    def __init__(self, path=None, cfg_path=None):
        os.makedirs(DATA, exist_ok=True)
        self.path = path or os.path.join(DATA, "bot.db")
        self.cfg_path = cfg_path or os.path.join(DATA, "config.json")
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS positions(id TEXT PRIMARY KEY, data TEXT);
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT, inst TEXT, symbol TEXT, side INTEGER, qty REAL,
            entry REAL, exit REAL, pnl REAL, fees REAL, t_open INTEGER, t_close INTEGER, reason TEXT, source TEXT, mode TEXT);
        CREATE TABLE IF NOT EXISTS equity(t INTEGER PRIMARY KEY, equity REAL, cash REAL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER, level TEXT, msg TEXT);
        CREATE TABLE IF NOT EXISTS thoughts(id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER, inst TEXT, symbol TEXT, kind TEXT, text TEXT);
        CREATE TABLE IF NOT EXISTS ai_log(id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER, inst TEXT, prompt TEXT, reply TEXT,
            take INTEGER, tokens_in INTEGER, tokens_out INTEGER, ms INTEGER);
        """)
        try:
            self.db.execute("ALTER TABLE trades ADD COLUMN ai_p REAL")
        except sqlite3.OperationalError:
            pass
        self.cfg = self._load_cfg()

    # --- config
    def _load_cfg(self):
        try:
            with open(self.cfg_path) as f:
                return deep_merge(DEFAULTS, json.load(f))
        except Exception:
            return copy.deepcopy(DEFAULTS)

    def save_cfg(self, new=None):
        with self.lock:
            if new is not None:
                if "instruments" in new:
                    self.cfg["instruments"] = new["instruments"]
                self.cfg = deep_merge(self.cfg, {k: v for k, v in new.items() if k != "instruments"})
            if not self.cfg.get("webhook_secret"):
                import secrets
                self.cfg["webhook_secret"] = secrets.token_urlsafe(16)
            with open(self.cfg_path, "w") as f:
                json.dump(self.cfg, f, indent=1)
            return self.cfg

    def migrate_watchlist(self):
        """Older configs: add name/category/favorite fields, and list the featured big companies once (watch-only)."""
        ins, ch = self.cfg["instruments"], False
        for i in ins:
            r = catalog.lookup(i["market"], i["symbol"]) or {}
            for k, v in (("name", r.get("name") or i["symbol"]), ("cat", r.get("cat") or ("crypto" if i["market"] == "futures" else i["market"])), ("fav", False)):
                if k not in i:
                    i[k], ch = v, True
        if not self.cfg["ui"].get("seeded_stocks"):
            have = {(i["market"], i["symbol"]) for i in ins}
            tf = ins[0]["tf"] if ins else "4h"
            for sym in catalog.FEATURED_STOCKS:
                if ("stock", sym) not in have:
                    ins.append(catalog.make_instrument(catalog.lookup("stock", sym), tf, False))
            self.cfg["ui"]["seeded_stocks"], ch = True, True
        if ch:
            self.save_cfg()

    def public_cfg(self):
        c = copy.deepcopy(self.cfg)
        for sec, k in SECRETS:
            c[sec]["has_" + k] = bool(c[sec].get(k))
            c[sec][k] = ""
        return c

    # --- kv
    def get(self, k, default=None):
        with self.lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return json.loads(r["v"]) if r else default

    def put(self, k, v):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES(?,?)", (k, json.dumps(v)))
            self.db.commit()

    # --- positions
    def positions(self):
        with self.lock:
            return {r["id"]: json.loads(r["data"]) for r in self.db.execute("SELECT * FROM positions")}

    def set_position(self, pid, data):
        with self.lock:
            if data is None:
                self.db.execute("DELETE FROM positions WHERE id=?", (pid,))
            else:
                self.db.execute("INSERT OR REPLACE INTO positions VALUES(?,?)", (pid, json.dumps(data)))
            self.db.commit()

    # --- trades / equity / events
    def add_trade(self, **t):
        with self.lock:
            cols = "inst,symbol,side,qty,entry,exit,pnl,fees,t_open,t_close,reason,source,mode,ai_p".split(",")
            self.db.execute("INSERT INTO trades(%s) VALUES(%s)" % (",".join(cols), ",".join("?" * len(cols))), [t.get(c) for c in cols])
            self.db.commit()

    def trades(self, limit=500):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM trades ORDER BY t_close DESC LIMIT ?", (limit,))]

    def add_equity(self, t, eq, cash):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO equity VALUES(?,?,?)", (t, eq, cash))
            self.db.commit()

    def equity_curve(self, limit=2000):
        with self.lock:
            rows = self.db.execute("SELECT t,equity FROM equity ORDER BY t DESC LIMIT ?", (limit,)).fetchall()
            return [[r["t"], r["equity"]] for r in reversed(rows)]

    def event(self, level, msg):
        with self.lock:
            self.db.execute("INSERT INTO events(t,level,msg) VALUES(?,?,?)", (int(time.time() * 1000), level, msg))
            self.db.execute("DELETE FROM events WHERE id < (SELECT MAX(id) FROM events) - 500")
            self.db.commit()
        notify.on_event(self.cfg, level, msg)

    def events(self, limit=60):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def think(self, inst, kind, text):
        """One line of the trader's visible reasoning (scan / candidate / decision / skip / open / manage / close)."""
        with self.lock:
            self.db.execute("INSERT INTO thoughts(t,inst,symbol,kind,text) VALUES(?,?,?,?,?)", (int(time.time() * 1000), inst.get("id"), inst.get("symbol", ""), kind, text[:400]))
            self.db.execute("DELETE FROM thoughts WHERE id < (SELECT MAX(id) FROM thoughts) - 800")
            self.db.commit()

    def thoughts(self, limit=60, after=0):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM thoughts WHERE id > ? ORDER BY id DESC LIMIT ?", (after, limit))]

    def ai_log_add(self, **r):
        with self.lock:
            self.db.execute("INSERT INTO ai_log(t,inst,prompt,reply,take,tokens_in,tokens_out,ms) VALUES(?,?,?,?,?,?,?,?)",
                            (int(time.time() * 1000), r.get("inst"), r.get("prompt"), r.get("reply"), r.get("take"),
                             r.get("tokens_in", 0), r.get("tokens_out", 0), r.get("ms", 0)))
            self.db.commit()

    def ai_log(self, limit=30):
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM ai_log ORDER BY id DESC LIMIT ?", (limit,))]
            tot = self.db.execute("SELECT COALESCE(SUM(tokens_in),0) a, COALESCE(SUM(tokens_out),0) b, COUNT(*) n FROM ai_log").fetchone()
            return rows, dict(tokens_in=tot["a"], tokens_out=tot["b"], calls=tot["n"])

    def scorecard(self):
        """Do the AI's follow-through ratings actually predict trade outcomes? Computed from real closed trades (free)."""
        with self.lock:
            rows = self.db.execute("SELECT ai_p, pnl FROM trades WHERE ai_p IS NOT NULL").fetchall()
        out = []
        for lo, hi, label in ((0, 40, "Rated low (<40)"), (40, 60, "Rated mid (40-59)"), (60, 101, "Rated high (60+)")):
            v = [r["pnl"] for r in rows if lo <= r["ai_p"] < hi]
            out.append(dict(label=label, n=len(v), win=(sum(1 for x in v if x > 0) / len(v) * 100) if v else None, avg=(sum(v) / len(v)) if v else None, total=sum(v)))
        return dict(rows=out, n=len(rows))

    def reset_paper(self):
        with self.lock:
            for t in ("positions", "trades", "equity", "events", "ai_log", "thoughts"):
                self.db.execute("DELETE FROM " + t)
            self.db.execute("DELETE FROM kv")
            self.db.commit()
