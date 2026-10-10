"""The three bot styles a user can choose between, with an honest evidence summary and a live scorecard each (from the trade journal)."""
import time
from . import scalper

INFO = {
    "trend": dict(
        name="Trend following", badge="Recommended", tone="good",
        tagline="Slow and steady: follows big moves on 4-hour candles across 38 coins.",
        how=["Four independent trend systems per coin, each with its own trade and trailing stop.",
             "Trades only when a 4-hour candle closes beyond its recent range, so about 10 to 15 trades a week across all coins.",
             "Risk per trade 0.3% (fees included), up to 15 open trades, daily-loss and drawdown brakes."],
        evidence="Tested on real candles after fees and slippage. Out-of-sample: about +13% and +19% in two separate year-long windows, worst dip 8 to 15%, Sharpe about 0.9. "
                 "The last 50 days were -9% (a choppy market): expect losing stretches.",
        sources=("engine", "tradingview")),
    "smart": dict(
        name="Smart-money day trader", badge="Experimental", tone="warn",
        tagline="Patient. Checks liquidity, waits for the big players to act, then follows. Few trades, bigger targets.",
        how=["Liquidity first: skips thin or wide-spread markets (crypto: 24h volume, spread, order-book depth; stocks: daily dollar volume).",
             "Confirmation sequence, never anticipation: 4-hour trend, then a liquidity sweep, then a strong high-volume candle that breaks structure, then a pullback that is rejected.",
             "Reads big-player footprints (open interest, top-trader positioning, large prints, order-book lean). Stop beyond the sweep; target the next liquidity pool (at least 2R).",
             "Stocks: 15-minute opening-range breakout with 1.5x relative volume, above VWAP, index agreeing; flat before the close.",
             "At most 3 trades a day. Optional AI sign-off on each confirmed setup."],
        evidence="Honest status: we back-tested the crypto confirmation sequence (4 coins, 2 years of 15-minute candles, about 750 setups, costs included): average -0.10R in-sample and "
                 "-0.27R out-of-sample, so it lost money. Opening-range breakouts on 46 stocks also lost, and big-player positioning had no market-neutral predictive power. "
                 "The live-only checks (order book, large prints) could not be back-tested. It runs on practice money with a scorecard so you can judge it yourself.",
        sources=("smart",)),
    "scalp": dict(
        name="Scalper (high frequency)", badge="Experimental", tone="warn",
        tagline="Many very small, very fast trades on 1-minute candles with limit orders. Practice mode only.",
        how=["Fades short-term over-extensions on BTC, ETH, SOL and XRP: resting limit orders (maker fees), a tiny target, a tight stop, out within 10 minutes.",
             "Dozens of trades a day. Fills are simulated conservatively: price must trade through your order, stops pay taker fees plus slippage.",
             "Trade size, fees (maker / taker / slippage), daily caps and the daily-loss limit are yours to set.",
             "Pauses itself if its last 40 trades average below zero after fees, and shows exactly how far fees are from break-even."],
        evidence="Honest status: in 75 days of 1-minute data none of 148 settings was profitable, even at a zero-maker-fee tier (best case about -1 bps per trade with a 73% win rate). "
                 "Scalping only works when your fees are lower than the small edge. Use the fee settings and the break-even readout to see where you stand.",
        sources=("scalp",)),
}


def _perf(trades, sources, since=0):
    tr = [t for t in trades if t.get("source") in sources and t["t_close"] >= since]
    if not tr:
        return dict(n=0, win=0.0, pnl=0.0, fees=0.0, week=0.0)
    wk = int(time.time() * 1000) - 7 * 86400000
    return dict(n=len(tr), win=sum(1 for t in tr if t["pnl"] > 0) / len(tr) * 100, pnl=sum(t["pnl"] for t in tr), fees=sum(t["fees"] for t in tr), week=sum(t["pnl"] for t in tr if t["t_close"] >= wk))


def overview(store, engine):
    trades = store.trades(100000)
    cfg = store.cfg
    poss = store.positions()
    out = {}
    for sid, info in INFO.items():
        since = (store.get("scalp_since") or 0) if sid == "scalp" else 0
        d = dict(id=sid, **{k: v for k, v in info.items() if k != "sources"})
        d["perf"] = _perf(trades, info["sources"], since)
        d["open"] = sum(1 for p in poss.values() if p.get("source") in info["sources"])
        if sid == "trend":
            mix = [i for i in cfg["instruments"] if i.get("strategy") == "trendmix" and i.get("enabled", True)]
            d["enabled"] = bool(engine.running)
            d["coins"] = len(mix)
            tfs = sorted({i["tf"] for i in cfg["instruments"] if i["market"] == "futures"})
            d["settings"] = dict(tf="/".join(tfs), risk=cfg["risk"]["per_trade_pct"], off_plan=bool(set(tfs) - {"4h", "1d"}) or cfg["risk"]["per_trade_pct"] >= 1.0)
        elif sid == "smart":
            d["enabled"] = bool(cfg["smart"]["enabled"])
            d["status"] = {k: dict(text=v[0], t=v[1]) for k, v in engine.smart.status.items()}
        else:
            d["enabled"] = bool(cfg["scalp"]["enabled"])
            st = scalper.stats(store)
            d["scalp"] = dict(st, breakeven=scalper.breakeven(st, cfg["scalp"]))
            d["pending"] = len(engine.scalper.pend)
        out[sid] = d
    return out
