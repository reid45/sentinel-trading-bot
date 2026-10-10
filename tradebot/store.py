"""SQLite persistence + JSON config (secrets stay on this machine in data/config.json)."""
import os, json, sqlite3, threading, time, copy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("TRADEBOT_DATA") or os.path.join(ROOT, "data")

PERP_PARAMS = dict(n=40, trend=0, stop=4.0, trail=1, tp=0, reverse=1)
FX_PARAMS = dict(fast=10, slow=100, adx=15, stop=3.0, trail=1, tp=0, reverse=1)


def _inst(market, sym, strat, params, on=True):
    return dict(id="%s:%s:4h" % (market, sym), market=market, symbol=sym, tf="4h", strategy=strat, params=dict(params), enabled=on)


DEFAULTS = {
    "mode": "paper",                      # paper | live
    "start_equity": 10000.0,
    "poll_seconds": 20,
    "risk": {"per_trade_pct": 0.5, "max_positions": 6, "max_leverage_per_pos": 3.0, "max_total_leverage": 3.0,
             "daily_loss_limit_pct": 3.0, "max_drawdown_halt_pct": 20.0},
    "instruments": [_inst("futures", s, "donchian", PERP_PARAMS) for s in
                    ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "LTCUSDT", "AVAXUSDT")] +
                   [_inst("forex", s, "emacross", FX_PARAMS) for s in ("EURUSD=X", "GBPUSD=X", "USDJPY=X")],
    "ai": {"mode": "advise", "provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
           "api_key": "", "timeout": 12, "min_p": 0, "tools": True, "max_tool_rounds": 2,
           "budget": {"max_calls_day": 120, "max_usd_day": 0.25, "max_usd_total": 3.0}},
    "broker": {"exchange": "bybit", "bybit_env": "demo", "bybit_key": "", "bybit_secret": "",
               "binance_key": "", "binance_secret": "", "binance_testnet": True},
    "webhook_secret": "",
}


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

    def public_cfg(self):
        c = copy.deepcopy(self.cfg)
        for sec, k in (("ai", "api_key"), ("broker", "binance_key"), ("broker", "binance_secret"), ("broker", "bybit_key"), ("broker", "bybit_secret")):
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

    def events(self, limit=60):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

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
            for t in ("positions", "trades", "equity", "events", "ai_log"):
                self.db.execute("DELETE FROM " + t)
            self.db.execute("DELETE FROM kv")
            self.db.commit()
