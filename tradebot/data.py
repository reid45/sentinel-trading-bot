"""Market data (public endpoints, no API keys): Binance spot/futures, Yahoo (forex). CSV-cached."""
import os, csv, json, threading, time, urllib.error, urllib.request, urllib.parse

DATA_DIR = os.environ.get("TRADEBOT_DATA") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TF_MS = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}
UA = {"User-Agent": "Mozilla/5.0"}

import collections
CALLS = collections.deque(maxlen=5000)       # timestamps of recent network requests: the health panel shows the rate so rate-limit headroom is visible


def calls_last_minute():
    now = time.time()
    return sum(1 for t in list(CALLS) if now - t < 60)


_ban = {"until": 0.0}                       # Binance answered 418/429: stay off the network until this time so the ban is never extended
_pace = {"lock": threading.Lock(), "t": 0.0}


def _pace_wait(gap=0.45):
    """Bulk (multi-page) downloads share one global pace, keeping request weight far below Binance's 2400/min limit."""
    with _pace["lock"]:
        now = time.time()
        time.sleep(max(0.0, _pace["t"] + gap - now))
        _pace["t"] = time.time()


def _get(url, timeout=15):
    binance = "binance" in url
    if binance and time.time() < _ban["until"]:
        raise RuntimeError("Binance asked us to slow down; paused for another %ds" % (_ban["until"] - time.time()))
    for attempt in range(3):
        try:
            CALLS.append(time.time())
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                _ban["until"] = time.time() + min(int(e.headers.get("Retry-After") or 60), 3600) + 1
                raise RuntimeError("Binance rate limit (HTTP %d); pausing %ds" % (e.code, _ban["until"] - time.time()))
            if attempt == 2: raise
            time.sleep(1 + attempt)
        except Exception:
            if attempt == 2: raise
            time.sleep(1 + attempt)

BYBIT_IV = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "4h": "240", "1d": "D"}
LAST_SRC = {}                    # market -> name of the source that served the latest request (shown in the health panel)


def _bybit_klines(symbol, tf, limit):
    d = _get("https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=%s&limit=%d" % (symbol, BYBIT_IV[tf], min(limit, 1000)))
    if d.get("retCode") != 0:
        raise RuntimeError("Bybit: %s" % d.get("retMsg"))
    return sorted((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in d["result"]["list"])


def _futures_recent(symbol, tf, limit, end):
    """Binance perps first; if Binance is down / banned / blocked, Bybit perps, then Binance spot (same price within a few bps)."""
    errs = []
    try:
        rows = _binance("https://fapi.binance.com", "/fapi/v1/klines", symbol, tf, end - limit * TF_MS[tf], end, 1500)
        LAST_SRC["futures"] = "Binance"
        return rows
    except Exception as e:
        errs.append("Binance: %s" % str(e)[:60])
    try:
        rows = _bybit_klines(symbol, tf, limit)
        LAST_SRC["futures"] = "Bybit (Binance unavailable)"
        return rows
    except Exception as e:
        errs.append("Bybit: %s" % str(e)[:60])
    try:
        rows = _binance("https://data-api.binance.vision", "/api/v3/klines", symbol, tf, end - limit * TF_MS[tf], end, 1000)
        LAST_SRC["futures"] = "Binance spot (futures feeds unavailable)"
        return rows
    except Exception as e:
        errs.append("spot: %s" % str(e)[:60])
    raise RuntimeError("no price feed reachable (" + "; ".join(errs) + ")")


def _binance(base, path, symbol, tf, start_ms, end_ms, limit):
    out, t = [], start_ms
    first = True
    while t < end_ms:
        if not first:
            _pace_wait()
        first = False
        rows = _get("%s%s?symbol=%s&interval=%s&startTime=%d&limit=%d" % (base, path, symbol, tf, t, limit))
        if not rows: break
        out += [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
        nt = rows[-1][0] + TF_MS[tf]
        if nt <= t or len(rows) < 2: break
        t = nt
    return out

def _yahoo(symbol, tf, years):
    iv = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d"}.get(tf)
    if tf == "4h": iv = "1h"
    rng = {"1m": "7d", "5m": "60d", "15m": "60d", "1h": "729d"}.get(iv, "%dy" % years)
    d = _get("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=%s&range=%s" % (urllib.parse.quote(symbol), iv, rng))
    res = (d.get("chart") or {}).get("result")
    if not res or "timestamp" not in res[0]:
        raise ValueError("no price data for %s" % symbol)
    r = res[0]; q = r["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(r["timestamp"]):
        if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]): continue
        rows.append((t * 1000, q["open"][i], q["high"][i], q["low"][i], q["close"][i], (q["volume"][i] or 0)))
    return rows

def _resample(rows, factor_ms):
    out, cur = [], None
    for t, o, h, l, c, v in rows:
        b = t - t % factor_ms
        if cur and cur[0] == b:
            cur[2] = max(cur[2], h); cur[3] = min(cur[3], l); cur[4] = c; cur[5] += v
        else:
            if cur: out.append(tuple(cur))
            cur = [b, o, h, l, c, v]
    if cur: out.append(tuple(cur))
    return out

def fetch(market, symbol, tf="4h", years=4, use_cache=True, max_age_s=3600):
    """market: crypto | futures | forex. Returns list of (ts_ms, o, h, l, c, v), oldest first, closed bars only."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "%s_%s_%s.csv" % (market, symbol.replace("=", "").replace("/", ""), tf))
    if use_cache and os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age_s:
        return load_csv(path)
    end = int(time.time() * 1000); start = end - years * 365 * 86400000
    if market == "crypto": rows = _binance("https://data-api.binance.vision", "/api/v3/klines", symbol, tf, start, end, 1000)
    elif market == "futures": rows = _binance("https://fapi.binance.com", "/fapi/v1/klines", symbol, tf, start, end, 1500)
    elif market in ("forex", "stock"):
        rows = _yahoo(symbol, tf, years)
        if tf == "4h": rows = _resample(rows, TF_MS["4h"])
    else: raise ValueError(market)
    rows = [r for r in rows if r[0] + TF_MS[tf] <= end]  # drop the still-forming bar
    seen, clean = set(), []
    for r in rows:
        if r[0] not in seen: seen.add(r[0]); clean.append(r)
    if clean:
        with open(path, "w", newline="") as f: csv.writer(f).writerows(clean)
    return clean

def load_csv(path):
    with open(path) as f:
        return [(int(r[0]), *map(float, r[1:])) for r in csv.reader(f)]


def fetch_recent(market, symbol, tf, limit=500, forming=False):
    """Latest closed bars (no cache) for the live engine."""
    end = int(time.time() * 1000)
    if market == "crypto":
        rows = _binance("https://data-api.binance.vision", "/api/v3/klines", symbol, tf, end - limit * TF_MS[tf], end, 1000)
    elif market == "futures":
        rows = _futures_recent(symbol, tf, limit, end)
    elif market in ("forex", "stock"):
        rows = _yahoo(symbol, tf, 1)
        if tf == "4h":
            rows = _resample(rows, TF_MS["4h"])
    else:
        raise ValueError(market)
    if forming:
        return rows[-limit:]          # includes the still-forming bar (charts only; strategies never see it)
    return [r for r in rows if r[0] + TF_MS[tf] <= end][-limit:]


_px = {"t": 0.0, "d": {}, "lock": threading.Lock()}


def all_prices(max_age=5.0):
    """Every Binance perp price in ONE request (cached a few seconds). 38 coins polled one by one cost 38 requests; this costs 1."""
    with _px["lock"]:
        if time.time() - _px["t"] > max_age:
            rows = _get("https://fapi.binance.com/fapi/v1/ticker/price")
            _px["d"], _px["t"] = {r["symbol"]: float(r["price"]) for r in rows}, time.time()
        return _px["d"]


def last_price(market, symbol):
    if market == "futures":
        try:
            p = all_prices().get(symbol)
            if p:
                LAST_SRC["futures"] = "Binance"
                return p
        except Exception:
            pass
        try:
            p = float(_get("https://fapi.binance.com/fapi/v1/ticker/price?symbol=%s" % symbol)["price"])
            LAST_SRC["futures"] = "Binance"
            return p
        except Exception:
            pass
        try:
            d = _get("https://api.bybit.com/v5/market/tickers?category=linear&symbol=%s" % symbol)
            LAST_SRC["futures"] = "Bybit (Binance unavailable)"
            return float(d["result"]["list"][0]["lastPrice"])
        except Exception:
            LAST_SRC["futures"] = "Binance spot (futures feeds unavailable)"
            return float(_get("https://data-api.binance.vision/api/v3/ticker/price?symbol=%s" % symbol)["price"])
    if market == "crypto":
        return float(_get("https://data-api.binance.vision/api/v3/ticker/price?symbol=%s" % symbol)["price"])
    d = _get("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1m&range=1d" % urllib.parse.quote(symbol))
    return float(d["chart"]["result"][0]["meta"]["regularMarketPrice"])
