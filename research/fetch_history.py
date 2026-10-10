"""Download public Binance futures candles used by the strategy studies (no API key). Run: py research/fetch_history.py"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from concurrent.futures import ThreadPoolExecutor
from tradebot import data

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "NEARUSDT", "SUIUSDT"]
MORE = ["TRXUSDT", "DOTUSDT", "ATOMUSDT", "UNIUSDT", "ETCUSDT", "FILUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "AAVEUSDT", "RUNEUSDT", "ALGOUSDT", "HBARUSDT", "ICPUSDT", "STXUSDT", "TONUSDT", "FETUSDT", "TIAUSDT", "SEIUSDT", "BCHUSDT", "XLMUSDT", "MKRUSDT", "LDOUSDT", "GRTUSDT", "IMXUSDT", "1000PEPEUSDT", "1000SHIBUSDT"]
JOBS = [(s, "1h", 3) for s in SYMS + MORE if not os.path.exists(os.path.join(data.DATA_DIR, "futures_%s_1h.csv" % s))]


def job(j):
    s, tf, yrs = j
    t = time.time()
    try:
        rows = data.fetch("futures", s, tf, yrs, use_cache=False)
        return "%s %s %d bars %.0fs" % (s, tf, len(rows), time.time() - t)
    except Exception as e:
        return "%s %s FAILED %s" % (s, tf, e)


if __name__ == "__main__":
    with ThreadPoolExecutor(2) as ex:
        for r in ex.map(job, JOBS):
            print(r, flush=True)
