"""Bybit V5 USDT linear perpetuals (one-way mode). env: demo (real prices, play money) | testnet | real.
Same interface as brokers.BinanceFutures. The stop is attached to the entry order and lives on the exchange."""
import hashlib, hmac, json, math, time, urllib.error, urllib.parse, urllib.request

BASES = {"demo": "https://api-demo.bybit.com", "testnet": "https://api-testnet.bybit.com", "real": "https://api.bybit.com"}


class Bybit:
    def __init__(self, key, secret, env="demo", base=None):
        self.key, self.secret = key, secret.encode()
        self.base = base or BASES[env]
        self._filters = {}

    def sign(self, ts, recv, payload):
        return hmac.new(self.secret, (str(ts) + self.key + str(recv) + payload).encode(), hashlib.sha256).hexdigest()

    def _req(self, method, path, params=None, signed=True, ok_codes=()):
        params = params or {}
        ts, recv = int(time.time() * 1000), 5000
        if method == "GET":
            qs = urllib.parse.urlencode(params)
            url, body, payload = self.base + path + ("?" + qs if qs else ""), None, qs
        else:
            body = json.dumps(params, separators=(",", ":"))
            url, payload = self.base + path, body
        h = {"Content-Type": "application/json"}
        if signed:
            h.update({"X-BAPI-API-KEY": self.key, "X-BAPI-TIMESTAMP": str(ts), "X-BAPI-RECV-WINDOW": str(recv),
                      "X-BAPI-SIGN": self.sign(ts, recv, payload)})
        req = urllib.request.Request(url, method=method, headers=h, data=body.encode() if body else None)
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=10).read())
        except urllib.error.HTTPError as e:
            raise RuntimeError("bybit http %s: %s" % (e.code, e.read()[:200]))
        if d.get("retCode") not in (0,) + tuple(ok_codes):
            raise RuntimeError("bybit %s: %s" % (d.get("retCode"), d.get("retMsg")))
        return d.get("result", {})

    def equity(self):
        r = self._req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        return float(r["list"][0]["totalEquity"])

    def _step(self, symbol):
        if symbol not in self._filters:
            r = self._req("GET", "/v5/market/instruments-info", {"category": "linear", "symbol": symbol}, signed=False)["list"][0]
            lot, pf = r["lotSizeFilter"], r["priceFilter"]
            self._filters[symbol] = (float(lot["qtyStep"]), float(pf["tickSize"]), float(lot["minOrderQty"]), lot["qtyStep"], pf["tickSize"])
        return self._filters[symbol]

    @staticmethod
    def _fmt(x, step_str):
        dec = len(step_str.split(".")[1].rstrip("0")) if "." in step_str else 0
        return "%.*f" % (dec, x)

    def round_qty(self, symbol, qty):
        step, _, mn, _, _ = self._step(symbol)
        q = math.floor(qty / step + 1e-9) * step
        return round(q, 8) if q >= mn else 0.0

    def _q(self, symbol, qty):
        return self._fmt(qty, self._step(symbol)[3])

    def _px(self, symbol, px):
        step = self._step(symbol)
        return self._fmt(round(px / step[1]) * step[1], step[4])

    def set_leverage(self, symbol, lev):
        self._req("POST", "/v5/position/set-leverage", {"category": "linear", "symbol": symbol, "buyLeverage": str(int(lev)),
                                                        "sellLeverage": str(int(lev))}, ok_codes=(110043,))

    def _avg_price(self, symbol):
        for _ in range(6):
            r = self._req("GET", "/v5/position/list", {"category": "linear", "symbol": symbol})["list"]
            if r and float(r[0].get("size") or 0) > 0:
                return float(r[0]["avgPrice"])
            time.sleep(0.3)
        return 0.0

    def open(self, symbol, side, qty, stop):
        self._req("POST", "/v5/order/create", {"category": "linear", "symbol": symbol, "side": "Buy" if side == 1 else "Sell",
                                               "orderType": "Market", "qty": self._q(symbol, qty), "stopLoss": self._px(symbol, stop),
                                               "slTriggerBy": "MarkPrice", "positionIdx": 0})
        return self._avg_price(symbol)

    def set_stop(self, symbol, side, stop):
        self._req("POST", "/v5/position/trading-stop", {"category": "linear", "symbol": symbol, "stopLoss": self._px(symbol, stop),
                                                         "slTriggerBy": "MarkPrice", "tpslMode": "Full", "positionIdx": 0}, ok_codes=(34040,))

    def close(self, symbol, side, qty):
        self._req("POST", "/v5/order/create", {"category": "linear", "symbol": symbol, "side": "Sell" if side == 1 else "Buy",
                                               "orderType": "Market", "qty": self._q(symbol, qty), "reduceOnly": True, "positionIdx": 0})
        try:
            ex = self._req("GET", "/v5/execution/list", {"category": "linear", "symbol": symbol, "limit": 1})["list"]
            return float(ex[0]["execPrice"]) if ex else 0.0
        except RuntimeError:
            return 0.0
