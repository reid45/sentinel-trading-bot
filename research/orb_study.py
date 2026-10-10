"""Patient, liquidity-confirmed day trading on US stocks: first-hour opening-range breakout with relative-volume confirmation.
Idea: do not guess the direction. Wait for the market's big players to show their hand (volume far above normal in the first hour), then trade only
a CLOSE beyond the opening range, in that direction, and be flat by the close. One trade per stock per day.
Data: Yahoo 1-hour bars (about 2 years). Costs: 3 bps slippage per side, no commission (typical for retail brokers; edit COST to test others).
Parameters are chosen on the first 60% of days and judged on the last 40%.   Run: py research/orb_study.py"""
import sys, os, json, itertools, time, calendar
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import data

UNI = ("SPY QQQ IWM AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM LLY V WMT XOM UNH MA ORCL COST HD PG NFLX JNJ BAC ABBV KO CRM CVX AMD MRK PEP CSCO ADBE MCD WFC DIS INTU "
       "QCOM IBM TXN GE CAT UBER PLTR COIN").split()
COST = 0.0003


def et_offset(ts_ms):
    """Hours behind UTC for US Eastern at that instant (DST: second Sunday of March 07:00 UTC .. first Sunday of November 06:00 UTC)."""
    t = time.gmtime(ts_ms / 1000)
    y = t.tm_year

    def nth_sunday(month, n):
        first = calendar.monthrange(y, month)[0]                      # weekday of the 1st (Mon=0)
        return 1 + (6 - first) % 7 + 7 * (n - 1)
    start = calendar.timegm((y, 3, nth_sunday(3, 2), 7, 0, 0))
    end = calendar.timegm((y, 11, nth_sunday(11, 1), 6, 0, 0))
    return 4 if start <= ts_ms / 1000 < end else 5


def days_of(rows):
    """Group 1h bars into regular-session days (9:30-16:00 ET). -> {date: [(hour_index, o, h, l, c, v), ...]}"""
    out = {}
    for t, o, h, l, c, v in rows:
        off = et_offset(t)
        lt = t / 1000 - off * 3600
        g = time.gmtime(lt)
        mins = g.tm_hour * 60 + g.tm_min
        if 9 * 60 + 30 <= mins < 16 * 60 and g.tm_wday < 5:
            out.setdefault((g.tm_year, g.tm_mon, g.tm_mday), []).append((mins, o, h, l, c, v))
    return {d: b for d, b in out.items() if len(b) >= 6 and b[0][0] == 570}


def load(sym):
    p = os.path.join(data.DATA_DIR, "stock_%s_1h.json" % sym)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 6 * 3600:
        return json.load(open(p))
    time.sleep(0.4)
    rows = data._yahoo(sym, "1h", 2)
    json.dump(rows, open(p, "w"))
    return rows


def trades_for(days, p):
    """-> list of (date, net_return) using parameter dict p."""
    keys = sorted(days)
    out = []
    first_vol = []
    for idx, d in enumerate(keys):
        bars = days[d]
        o1, h1, l1, c1, v1 = bars[0][1], bars[0][2], bars[0][3], bars[0][4], bars[0][5]
        hist = first_vol[-20:]
        rvol = v1 / (sum(hist) / len(hist)) if len(hist) >= 10 and sum(hist) else None
        first_vol.append(v1)
        if rvol is None or rvol < p["rvol"] or h1 <= l1:
            continue
        rng = h1 - l1
        if rng / c1 < p["min_rng"]:
            continue
        pos = None
        for k in range(1, len(bars) - 1):
            _, o, h, l, c, v = bars[k]
            if c > h1 and (p["dir"] in ("both", "long")):
                sd = 1
            elif c < l1 and (p["dir"] in ("both", "short")):
                sd = -1
            else:
                continue
            if p["vwap_ok"]:                                              # follow only if the close is on the right side of the session's volume-weighted price
                num = sum((b[2] + b[3] + b[4]) / 3 * b[5] for b in bars[:k + 1])
                den = sum(b[5] for b in bars[:k + 1]) or 1
                if (c - num / den) * sd <= 0:
                    continue
            entry = bars[k + 1][1] * (1 + COST * sd)
            stop = (l1 if sd == 1 else h1) if p["stop"] == "or" else entry - sd * p["stop_x"] * rng
            risk = abs(entry - stop)
            if risk <= 0 or risk / entry > 0.04:
                break
            tgt = entry + sd * p["tgt"] * risk if p["tgt"] else None
            ex = None
            for j in range(k + 1, len(bars)):
                _, oo, hh, ll, cc, _v = bars[j]
                if (sd == 1 and ll <= stop) or (sd == -1 and hh >= stop):
                    ex = min(stop, oo) if sd == 1 else max(stop, oo)
                    break
                if tgt and ((sd == 1 and hh >= tgt) or (sd == -1 and ll <= tgt)):
                    ex = tgt
                    break
            if ex is None:
                ex = bars[-1][4]
            ex *= (1 - COST * sd)
            out.append((d, sd * (ex - entry) / entry, sd * (ex - entry) / risk))
            break
    return out


GRID = dict(rvol=[0.0, 1.5, 2.0, 3.0], min_rng=[0.002, 0.005], dir=["both", "long"], vwap_ok=[False, True], stop=["or"], stop_x=[1.0], tgt=[0, 1.0, 2.0])


def combos(g):
    ks = list(g)
    return [dict(zip(ks, v)) for v in itertools.product(*[g[k] for k in ks])]


def main():
    allrows = {}
    for s in UNI:
        try:
            allrows[s] = days_of(load(s))
        except Exception as e:
            print("skip", s, str(e)[:50])
    alldays = sorted({d for v in allrows.values() for d in v})
    cut = alldays[int(len(alldays) * 0.6)]
    print("stocks:", len(allrows), "| days:", len(alldays), "| out-of-sample starts", cut, flush=True)
    rows = []
    for p in combos(GRID):
        r_is = [x for s, dd in allrows.items() for x in trades_for(dd, p) if x[0] < cut]
        if len(r_is) < 300:
            continue
        rows.append((sum(x[1] for x in r_is) / len(r_is), p, len(r_is)))
    rows.sort(key=lambda r: -r[0])
    print("%d/%d settings profitable in-sample" % (sum(1 for r in rows if r[0] > 0), len(rows)))
    out = []
    for avg, p, n in rows[:6]:
        r_o = [x for s, dd in allrows.items() for x in trades_for(dd, p) if x[0] >= cut]
        if not r_o:
            continue
        ao = sum(x[1] for x in r_o) / len(r_o)
        win = sum(1 for x in r_o if x[1] > 0) / len(r_o) * 100
        avgR = sum(x[2] for x in r_o) / len(r_o)
        per_day = len(r_o) / max(1, len([d for d in alldays if d >= cut]))
        print("  IS %+6.1f bps n=%4d | OOS %+6.1f bps/trade  win %2.0f%%  avg %+.2fR  n=%4d (%.1f/day) | %s" % (avg * 1e4, n, ao * 1e4, win, avgR, len(r_o), per_day, p), flush=True)
        out.append(dict(p=p, ins_bps=avg * 1e4, oos_bps=ao * 1e4, oos_n=len(r_o), oos_win=win, oos_R=avgR))
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "orb_study.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
