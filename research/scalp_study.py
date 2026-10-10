"""Can high-frequency scalping on 1-minute candles pay after realistic costs? Limit-order (maker) entries and exits are modelled conservatively:
a resting limit order only fills when price trades THROUGH it, stops are taker orders with slippage, same-bar ties go against us.
Parameters are chosen on the first 60% of each coin's 75 days and judged on the last 40%.   Run: py research/scalp_study.py"""
import sys, os, json, itertools, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import data, indicators as I

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
FEES = {"retail (maker 2bps / taker 5bps)": (0.0002, 0.0005), "low-fee tier (maker 0 / taker 2.5bps)": (0.0, 0.00025)}
SLIP = 0.0001                       # extra slippage on taker fills (1 bp)


def load(sym):
    p = os.path.join(data.DATA_DIR, "micro_%s_1m.json" % sym)
    return json.load(open(p)) if os.path.exists(p) else None


class Feat:
    def __init__(self, rows):
        self.r = rows
        self.o, self.h, self.l, self.c, self.v, self.tb = [[x[k] for x in rows] for k in (1, 2, 3, 4, 5, 6)]
        n = len(rows)
        self.atr = I.atr(self.h, self.l, self.c, 14)
        self.ma = I.sma(self.c, 20)
        self.sd = [None] * n
        for i in range(20, n):
            m = self.ma[i]
            self.sd[i] = (sum((x - m) ** 2 for x in self.c[i - 19:i + 1]) / 20) ** 0.5
        self.rsi = I.rsi(self.c, 3)
        self.vavg = I.sma(self.v, 30)


def signals(F, fam, p):
    """-> list of (i, side, kind, limit_px) decided at the close of bar i."""
    out = []
    n = len(F.r)
    for i in range(40, n - 1):
        a = F.atr[i]
        if not a or F.sd[i] is None:
            continue
        if fam == "mr":
            if F.c[i] < F.ma[i] - p["k"] * F.sd[i] and F.rsi[i] is not None and F.rsi[i] < p["rsi"]:
                out.append((i, 1, "maker", F.c[i] - p["off"] * a))
            elif F.c[i] > F.ma[i] + p["k"] * F.sd[i] and F.rsi[i] is not None and F.rsi[i] > 100 - p["rsi"]:
                out.append((i, -1, "maker", F.c[i] + p["off"] * a))
        elif fam == "brk":
            if F.c[i] > max(F.h[i - 20:i]) and F.vavg[i] and F.v[i] >= p["vol"] * F.vavg[i]:
                out.append((i, 1, "taker", None))
            elif F.c[i] < min(F.l[i - 20:i]) and F.vavg[i] and F.v[i] >= p["vol"] * F.vavg[i]:
                out.append((i, -1, "taker", None))
        elif fam == "flow":
            v3 = sum(F.v[i - 2:i + 1]) or 1
            share = sum(F.tb[i - 2:i + 1]) / v3
            mv = (F.c[i] - F.c[i - 3]) / a
            if share >= p["share"] and mv < p["absorb"]:                    # heavy taker buying that did not move price: absorption -> fade (maker)
                out.append((i, -1, "maker", F.c[i] + p["off"] * a))
            elif share <= 1 - p["share"] and mv > -p["absorb"]:
                out.append((i, 1, "maker", F.c[i] - p["off"] * a))
            elif share >= p["share"] and mv >= p["follow"]:                  # heavy taker buying AND price moving: momentum (taker)
                out.append((i, 1, "taker", None))
            elif share <= 1 - p["share"] and mv <= -p["follow"]:
                out.append((i, -1, "taker", None))
    return out


def simulate(F, sigs, p, fees):
    """One position at a time. Returns list of net returns (fraction of notional) per trade."""
    mk, tk = fees
    n = len(F.r)
    res, free = [], 0
    for i, sd, kind, lim in sigs:
        if i < free:
            continue
        a = F.atr[i]
        # ---- entry
        j, entry, fee_in = None, None, 0.0
        if kind == "maker":
            for k in range(i + 1, min(n, i + 1 + p["wait"])):
                if (sd == 1 and F.l[k] < lim) or (sd == -1 and F.h[k] > lim):
                    j, entry, fee_in = k, lim, mk
                    break
            if j is None:
                continue
        else:
            j, entry, fee_in = i + 1, F.o[i + 1] * (1 + SLIP * sd), tk
        tgt, stp = entry + sd * p["tgt"] * a, entry - sd * p["stop"] * a
        exit_px, fee_out, end = None, 0.0, min(n - 1, j + p["hold"])
        for k in range(j, end + 1):
            hit_stop = (sd == 1 and F.l[k] <= stp) or (sd == -1 and F.h[k] >= stp)
            hit_tgt = (sd == 1 and F.h[k] > tgt) or (sd == -1 and F.l[k] < tgt)
            if hit_stop:                                        # stop first on ties (conservative); taker with slippage; gap-through fills at the open
                px = min(stp, F.o[k]) if sd == 1 else max(stp, F.o[k])
                exit_px, fee_out = px * (1 - SLIP * sd), tk
                end = k
                break
            if hit_tgt:
                exit_px, fee_out, end = tgt, mk, k
                break
        if exit_px is None:
            exit_px, fee_out = F.c[end] * (1 - SLIP * sd), tk
        res.append(sd * (exit_px - entry) / entry - fee_in - fee_out)
        free = end + 1
    return res


GRID = {
    "mr": dict(k=[2.0, 2.5, 3.0], rsi=[10, 20], off=[0.0, 0.3], wait=[3], tgt=[0.6, 1.0, 1.5], stop=[1.0, 2.0], hold=[10]),
    "brk": dict(vol=[2.0, 3.0], wait=[1], tgt=[0.8, 1.2, 2.0], stop=[0.8, 1.2], hold=[10]),
    "flow": dict(share=[0.62, 0.7], absorb=[0.3, 0.8], follow=[1.5, 2.5], off=[0.0, 0.3], wait=[3], tgt=[0.6, 1.0], stop=[1.0, 1.5], hold=[10]),
}


def combos(g):
    ks = list(g)
    return [dict(zip(ks, v)) for v in itertools.product(*[g[k] for k in ks])]


def stats(rs, days):
    if not rs:
        return dict(n=0, avg_bps=0.0, win=0.0, per_day=0.0, tot=0.0)
    return dict(n=len(rs), avg_bps=sum(rs) / len(rs) * 1e4, win=sum(1 for x in rs if x > 0) / len(rs) * 100, per_day=len(rs) / days, tot=sum(rs) * 100)


def main():
    data_ = {s: load(s) for s in SYMS}
    data_ = {s: r for s, r in data_.items() if r}
    feats = {s: Feat(r) for s, r in data_.items()}
    days_is = {s: len(r) * 0.6 / 1440 for s, r in data_.items()}
    days_oos = {s: len(r) * 0.4 / 1440 for s, r in data_.items()}
    print("coins:", list(data_), "| bars per coin:", {s: len(r) for s, r in data_.items()}, flush=True)
    out = {}
    for fee_name, fees in FEES.items():
        print("\n=== fees: %s ===" % fee_name, flush=True)
        for fam, grid in GRID.items():
            rows = []
            for p in combos(grid):
                is_all, n_is = [], 0
                for s, F in feats.items():
                    k = int(len(F.r) * 0.6)
                    sg = [x for x in signals(F, fam, p) if x[0] < k]
                    is_all += simulate(F, sg, p, fees)
                st = stats(is_all, sum(days_is.values()))
                if st["n"] >= 150:
                    rows.append((st["avg_bps"], p, st))
            rows.sort(key=lambda r: -r[0])
            pos = sum(1 for r in rows if r[0] > 0)
            print("%s: %d/%d settings profitable in-sample" % (fam, pos, len(rows)), flush=True)
            for _, p, st in rows[:3]:
                oos_all = []
                for s, F in feats.items():
                    k = int(len(F.r) * 0.6)
                    sg = [x for x in signals(F, fam, p) if x[0] >= k]
                    oos_all += simulate(F, sg, p, fees)
                so = stats(oos_all, sum(days_oos.values()))
                print("   IS %+6.2f bps/trade win %2.0f%% %5.1f trades/day | OOS %+6.2f bps/trade win %2.0f%% %5.1f trades/day total %+6.1f%% | %s" % (
                    st["avg_bps"], st["win"], st["per_day"], so["avg_bps"], so["win"], so["per_day"], so["tot"], p), flush=True)
                out.setdefault(fee_name, {}).setdefault(fam, []).append(dict(p=p, ins=st, oos=so))
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "scalp_study.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
