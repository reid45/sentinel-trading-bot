"""Market catalog: a big curated list (crypto perps, stocks, ETFs, indices, commodities, forex), every live Binance USDT perpetual,
and Yahoo Finance symbol search for anything else. Also builds instrument configs with sensible per-market defaults."""
import copy, json, os, time, urllib.parse
from . import data

CATS = [("crypto", "Crypto"), ("stock", "Stocks"), ("etf", "ETFs"), ("forex", "Forex"), ("commodity", "Commodities"), ("index", "Indices")]


def _rows(cat, market, text):
    """'AAPL Apple' per line -> catalog rows."""
    out = []
    for line in text.strip().splitlines():
        sym, _, name = line.strip().partition(" ")
        out.append(dict(symbol=sym, name=name.strip() or sym, cat=cat, market=market))
    return out


CRYPTO = _rows("crypto", "futures", """
BTCUSDT Bitcoin
ETHUSDT Ethereum
BNBUSDT BNB
SOLUSDT Solana
XRPUSDT XRP
DOGEUSDT Dogecoin
ADAUSDT Cardano
AVAXUSDT Avalanche
LINKUSDT Chainlink
LTCUSDT Litecoin
DOTUSDT Polkadot
TRXUSDT TRON
BCHUSDT Bitcoin Cash
UNIUSDT Uniswap
ATOMUSDT Cosmos
ETCUSDT Ethereum Classic
XLMUSDT Stellar
NEARUSDT NEAR Protocol
APTUSDT Aptos
ARBUSDT Arbitrum
OPUSDT Optimism
INJUSDT Injective
SUIUSDT Sui
SEIUSDT Sei
TIAUSDT Celestia
FILUSDT Filecoin
AAVEUSDT Aave
MKRUSDT Maker
RUNEUSDT THORChain
ALGOUSDT Algorand
HBARUSDT Hedera
ICPUSDT Internet Computer
IMXUSDT Immutable
GRTUSDT The Graph
LDOUSDT Lido DAO
STXUSDT Stacks
TONUSDT Toncoin
1000PEPEUSDT Pepe
WIFUSDT dogwifhat
1000BONKUSDT Bonk
1000SHIBUSDT Shiba Inu
FETUSDT Fetch.ai
RENDERUSDT Render
JUPUSDT Jupiter
ENAUSDT Ethena
TAOUSDT Bittensor
ONDOUSDT Ondo
WLDUSDT Worldcoin
ORDIUSDT ORDI
PENDLEUSDT Pendle
""")
STOCKS = _rows("stock", "stock", """
AAPL Apple
MSFT Microsoft
NVDA NVIDIA
AMZN Amazon
GOOGL Alphabet (Google)
META Meta Platforms
TSLA Tesla
AVGO Broadcom
BRK-B Berkshire Hathaway
LLY Eli Lilly
JPM JPMorgan Chase
V Visa
WMT Walmart
XOM Exxon Mobil
UNH UnitedHealth
MA Mastercard
ORCL Oracle
COST Costco
HD Home Depot
PG Procter & Gamble
NFLX Netflix
JNJ Johnson & Johnson
BAC Bank of America
ABBV AbbVie
KO Coca-Cola
CRM Salesforce
CVX Chevron
AMD Advanced Micro Devices
MRK Merck
PEP PepsiCo
CSCO Cisco
ADBE Adobe
MCD McDonald's
WFC Wells Fargo
DIS Disney
INTU Intuit
QCOM Qualcomm
IBM IBM
TXN Texas Instruments
GE GE Aerospace
CAT Caterpillar
PFE Pfizer
AMAT Applied Materials
UBER Uber
BKNG Booking Holdings
NOW ServiceNow
ISRG Intuitive Surgical
INTC Intel
PLTR Palantir
COIN Coinbase
SHOP Shopify
PYPL PayPal
SNOW Snowflake
SMCI Super Micro Computer
ARM Arm Holdings
MU Micron
NKE Nike
SBUX Starbucks
BA Boeing
T AT&T
VZ Verizon
F Ford
GM General Motors
RIVN Rivian
SOFI SoFi
HOOD Robinhood
RBLX Roblox
SNAP Snap
PINS Pinterest
ROKU Roku
DKNG DraftKings
MARA Marathon Digital
RIOT Riot Platforms
MSTR Strategy (MicroStrategy)
LMT Lockheed Martin
GS Goldman Sachs
MS Morgan Stanley
C Citigroup
TSM Taiwan Semiconductor
ASML ASML
BABA Alibaba
NIO NIO
SPOT Spotify
ABNB Airbnb
DASH DoorDash
CRWD CrowdStrike
PANW Palo Alto Networks
""")
ETFS = _rows("etf", "stock", """
SPY SPDR S&P 500
QQQ Invesco QQQ (Nasdaq 100)
IWM Russell 2000
DIA Dow Jones ETF
VTI Vanguard Total Market
VOO Vanguard S&P 500
GLD Gold ETF
SLV Silver ETF
TLT 20+ Year Treasury
HYG High Yield Bonds
XLK Technology Sector
XLF Financials Sector
XLE Energy Sector
XLV Health Care Sector
SMH Semiconductors
ARKK ARK Innovation
SOXL Semiconductors 3x
TQQQ Nasdaq 3x
SQQQ Nasdaq -3x
UVXY Volatility ETF
IBIT iShares Bitcoin Trust
ETHA iShares Ethereum Trust
EEM Emerging Markets
EFA Developed Markets
""")
INDICES = _rows("index", "stock", """
^GSPC S&P 500
^NDX Nasdaq 100
^DJI Dow Jones
^RUT Russell 2000
^VIX Volatility Index
^FTSE FTSE 100
^GDAXI DAX
^N225 Nikkei 225
^HSI Hang Seng
""")
COMMODITIES = _rows("commodity", "stock", """
CL=F Crude Oil (WTI)
BZ=F Brent Crude
GC=F Gold
SI=F Silver
HG=F Copper
NG=F Natural Gas
ZC=F Corn
ZW=F Wheat
ZS=F Soybeans
""")
FOREX = _rows("forex", "forex", """
EURUSD=X Euro / US Dollar
GBPUSD=X British Pound / US Dollar
USDJPY=X US Dollar / Japanese Yen
AUDUSD=X Australian Dollar / US Dollar
USDCAD=X US Dollar / Canadian Dollar
USDCHF=X US Dollar / Swiss Franc
NZDUSD=X New Zealand Dollar / US Dollar
EURGBP=X Euro / British Pound
EURJPY=X Euro / Japanese Yen
GBPJPY=X British Pound / Japanese Yen
AUDJPY=X Australian Dollar / Japanese Yen
EURCHF=X Euro / Swiss Franc
USDMXN=X US Dollar / Mexican Peso
USDZAR=X US Dollar / South African Rand
USDSGD=X US Dollar / Singapore Dollar
""")
STATIC = CRYPTO + STOCKS + ETFS + INDICES + COMMODITIES + FOREX
FEATURED_STOCKS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]      # shown on the home page (watch-only) by default
_by_key = {(r["market"], r["symbol"]): r for r in STATIC}
_dyn = {"t": 0, "rows": []}


def lookup(market, symbol):
    return _by_key.get((market, symbol))


def binance_perps():
    """Every live USDT perpetual on Binance (cached 6h in memory and on disk). Falls back to the curated crypto list when offline."""
    if _dyn["rows"] and time.time() - _dyn["t"] < 6 * 3600:
        return _dyn["rows"]
    path = os.path.join(data.DATA_DIR, "binance_perps.json")
    try:
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * 3600:
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
        else:
            info = data._get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10)
            rows = [dict(symbol=s["symbol"], name=_by_key.get(("futures", s["symbol"]), {}).get("name") or s["baseAsset"], cat="crypto", market="futures")
                    for s in info["symbols"] if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"]
            os.makedirs(data.DATA_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(rows, f)
        _dyn.update(t=time.time(), rows=rows)
        return rows
    except Exception:
        return CRYPTO


YQ_MAP = {"EQUITY": ("stock", "stock"), "ETF": ("etf", "stock"), "INDEX": ("index", "stock"), "CURRENCY": ("forex", "forex"), "FUTURE": ("commodity", "stock")}


def yahoo_search(q, limit=12):
    try:
        d = data._get("https://query2.finance.yahoo.com/v1/finance/search?q=%s&quotesCount=%d&newsCount=0&listsCount=0" % (urllib.parse.quote(q), limit), timeout=6)
    except Exception:
        return []
    out = []
    for r in d.get("quotes", []):
        cat = YQ_MAP.get(r.get("quoteType"))
        if not cat or not r.get("symbol"):
            continue
        out.append(dict(symbol=r["symbol"], name=r.get("longname") or r.get("shortname") or r["symbol"], cat=cat[0], market=cat[1]))
    return out


def search(q="", cat="all", limit=60, online=True):
    """Curated + live Binance perps (+ Yahoo search for text queries). Matches symbol or name, prefix matches first."""
    q = (q or "").strip().lower()
    pool = list(STATIC)
    if cat in ("all", "crypto"):
        seen = {r["symbol"] for r in pool if r["market"] == "futures"}
        pool += [r for r in binance_perps() if r["symbol"] not in seen]
    rows = [r for r in pool if (cat in ("all", r["cat"])) and (not q or q in r["symbol"].lower() or q in r["name"].lower())]
    if q:
        rows.sort(key=lambda r: (not (r["symbol"].lower().startswith(q) or r["name"].lower().startswith(q)), r["symbol"]))
    have = {(r["market"], r["symbol"]) for r in rows}
    if online and len(q) >= 2 and len(rows) < limit:
        for r in yahoo_search(q):
            if (r["market"], r["symbol"]) not in have and (cat in ("all", r["cat"])):
                rows.append(r)
                have.add((r["market"], r["symbol"]))
    return rows[:limit]


# ---------------------------------------------------------------- instrument defaults
PERP = dict(n=40, trend=0, stop=4.0, trail=1, tp=0, reverse=1)
# Four independent 4h trend systems per coin, picked from the walk-forward studies in research/ (see docs/STRATEGY_STUDY.md).
TRENDMIX = dict(members=[
    dict(name="donch20", strat="donchian", p=dict(n=20, trend=200, stop=5.0, trail=1, tp=0, reverse=1)),
    dict(name="donch40", strat="donchian", p=dict(n=40, trend=0, stop=4.0, trail=1, tp=0, reverse=1)),
    dict(name="mtf1d", strat="mtf_break", p=dict(n=20, htf="1d", fast=20, slow=50, adx=18, stop=3.0, trail=1, tp=0, reverse=0)),
    dict(name="ema20_150", strat="emacross", p=dict(fast=20, slow=150, adx=15, stop=4.5, trail=1, tp=0, reverse=1))], trail=1)
# 38 liquid USDT perpetuals from the study (priority order when position slots are scarce). TON and MKR were dropped: Binance serves frozen zero-volume candles for them.
UNIVERSE = ("BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT ADAUSDT AVAXUSDT LINKUSDT LTCUSDT NEARUSDT SUIUSDT TRXUSDT DOTUSDT ATOMUSDT UNIUSDT ETCUSDT FILUSDT "
            "APTUSDT ARBUSDT OPUSDT INJUSDT AAVEUSDT RUNEUSDT ALGOUSDT HBARUSDT ICPUSDT STXUSDT FETUSDT TIAUSDT SEIUSDT BCHUSDT XLMUSDT LDOUSDT GRTUSDT IMXUSDT "
            "1000PEPEUSDT 1000SHIBUSDT").split()
FX = dict(fast=10, slow=100, adx=15, stop=3.0, trail=1, tp=0, reverse=1)
STOCK = dict(n=55, trend=200, stop=3.0, trail=1, tp=0, reverse=0)


def defaults_for(market):
    """(strategy, params) for a new market. Stocks are long-only (no shorting) so reverse is off."""
    if market == "forex":
        return "emacross", dict(FX)
    if market == "stock":
        return "donchian", dict(STOCK)
    return "trendmix", copy.deepcopy(TRENDMIX)


def make_instrument(row, tf="4h", enabled=True, fav=False):
    strat, params = defaults_for(row["market"])
    return dict(id="%s:%s:%s" % (row["market"], row["symbol"], tf), market=row["market"], symbol=row["symbol"], name=row.get("name") or row["symbol"],
                cat=row.get("cat") or ("crypto" if row["market"] == "futures" else row["market"]), tf=tf, strategy=strat, params=params, enabled=enabled, fav=fav)
