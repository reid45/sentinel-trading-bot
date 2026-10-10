"""Live broker adapter: Binance USDT-M futures (REST, HMAC-signed). EXPERIMENTAL - exercise it on the testnet first
(https://testnet.binancefuture.com). Paper trading needs no broker; the engine simulates fills itself."""
import hashlib, hmac, json, math, time, urllib.error, urllib.parse, urllib.request


class BinanceFutures:
    def __init__(self, key, secret, testnet=True):
        self.key, self.secret = key, secret.encode()
        self.base = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        self._filters = {}

    def sign(self, params):
        q = urllib.parse.urlencode(params)
        return q + "&signature=" + hmac.new(self.secret, q.encode(), hashlib.sha256).hexdigest()

    def _req(self, method, path, params=None, signed=True):
        params = dict(params or {})
        if signed:
            params.update(timestamp=int(time.time() * 1000), recvWindow=5000)
            q = self.sign(params)
        else:
            q = urllib.parse.urlencode(params)
        req = urllib.request.Request(self.base + path + "?" + q, method=method, headers={"X-MBX-APIKEY": self.key})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=10).read())
        except urllib.error.HTTPError as e:
            raise RuntimeError("binance %s: %s" % (e.code, e.read()[:200]))

    def equity(self):
        return float(self._req("GET", "/fapi/v2/account")["totalMarginBalance"])

    def _step(self, symbol):
        if symbol not in self._filters:
            for s in self._req("GET", "/fapi/v1/exchangeInfo", signed=False)["symbols"]:
                f = {x["filterType"]: x for x in s["filters"]}
                self._filters[s["symbol"]] = (float(f["LOT_SIZE"]["stepSize"]), float(f["PRICE_FILTER"]["tickSize"]),
                                              float(f["LOT_SIZE"]["minQty"]))
        return self._filters[symbol]

    def round_qty(self, symbol, qty):
        step, _, mn = self._step(symbol)
        q = math.floor(qty / step) * step
        return round(q, 8) if q >= mn else 0.0

    def round_px(self, symbol, px):
        _, tick, _ = self._step(symbol)
        return round(round(px / tick) * tick, 8)

    def set_leverage(self, symbol, lev):
        self._req("POST", "/fapi/v1/leverage", dict(symbol=symbol, leverage=int(lev)))

    def open(self, symbol, side, qty, stop):
        """Market entry + exchange-side protective stop. Returns average fill price."""
        o = self._req("POST", "/fapi/v1/order", dict(symbol=symbol, side="BUY" if side == 1 else "SELL", type="MARKET", quantity=qty,
                                                    newOrderRespType="RESULT"))
        fill = float(o.get("avgPrice") or 0)
        self.set_stop(symbol, side, stop)
        return fill

    def set_stop(self, symbol, side, stop):
        try:
            self._req("DELETE", "/fapi/v1/allOpenOrders", dict(symbol=symbol))
        except RuntimeError:
            pass
        self._req("POST", "/fapi/v1/order", dict(symbol=symbol, side="SELL" if side == 1 else "BUY", type="STOP_MARKET",
                                                stopPrice=self.round_px(symbol, stop), closePosition="true", workingType="MARK_PRICE"))

    def close(self, symbol, side, qty):
        o = self._req("POST", "/fapi/v1/order", dict(symbol=symbol, side="SELL" if side == 1 else "BUY", type="MARKET", quantity=qty,
                                                    reduceOnly="true", newOrderRespType="RESULT"))
        try:
            self._req("DELETE", "/fapi/v1/allOpenOrders", dict(symbol=symbol))
        except RuntimeError:
            pass
        return float(o.get("avgPrice") or 0)


def make_broker(b):
    """Pick the exchange adapter from config; None when keys are missing."""
    if b.get("exchange") == "binance":
        if not b.get("binance_key") or not b.get("binance_secret"):
            return None
        return BinanceFutures(b["binance_key"], b["binance_secret"], b["binance_testnet"])
    if not b.get("bybit_key") or not b.get("bybit_secret"):
        return None
    from .bybit import Bybit
    return Bybit(b["bybit_key"], b["bybit_secret"], b.get("bybit_env", "demo"))
