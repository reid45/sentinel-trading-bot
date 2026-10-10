"""Market data (public endpoints, no API keys): Binance spot/futures, Yahoo (forex). CSV-cached."""
import os, csv, json, time, urllib.request, urllib.parse

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
TF_MS = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}
UA = {"User-Agent": "Mozilla/5.0"}

def _get(url, timeout=15):
    for attempt in range(3):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read())
        except Exception:
            if attempt == 2: raise
            time.sleep(1 + attempt)

def _binance(base, path, symbol, tf, start_ms, end_ms, limit):
    out, t = [], start_ms
    while t < end_ms:
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
        rows = _binance("https://fapi.binance.com", "/fapi/v1/klines", symbol, tf, end - limit * TF_MS[tf], end, 1500)
    elif market in ("forex", "stock"):
        rows = _yahoo(symbol, tf, 1)
        if tf == "4h":
            rows = _resample(rows, TF_MS["4h"])
    else:
        raise ValueError(market)
    if forming:
        return rows[-limit:]          # includes the still-forming bar (charts only; strategies never see it)
    return [r for r in rows if r[0] + TF_MS[tf] <= end][-limit:]


def last_price(market, symbol):
    if market == "futures":
        return float(_get("https://fapi.binance.com/fapi/v1/ticker/price?symbol=%s" % symbol)["price"])
    if market == "crypto":
        return float(_get("https://data-api.binance.vision/api/v3/ticker/price?symbol=%s" % symbol)["price"])
    d = _get("https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1m&range=1d" % urllib.parse.quote(symbol))
    return float(d["chart"]["result"][0]["meta"]["regularMarketPrice"])
