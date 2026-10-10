"""Download 1-minute candles (with taker-buy volume) for the scalping study. Paced; run: py research/fetch_micro.py"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import data

DAYS = 75
for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"):
    end = int(time.time() * 1000)
    t, rows = end - DAYS * 86400000, []
    while t < end:
        data._pace_wait(0.5)
        k = data._get("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=1m&startTime=%d&limit=1500" % (sym, t))
        if not k:
            break
        rows += [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[9])) for r in k]
        t = k[-1][0] + 60000
        if len(k) < 2:
            break
    rows = [r for r in rows if r[0] + 60000 <= end]
    with open(os.path.join(data.DATA_DIR, "micro_%s_1m.json" % sym), "w") as f:
        json.dump(rows, f)
    print(sym, len(rows), "bars", flush=True)
