"""The scalper's targets (0.6 to 1.5 ATR, a few basis points) were smaller than its round-trip fees, so most 'winners' still lost money. This re-tests the mean-reversion
fade with targets and stops that are big enough to clear fees (2 to 6 ATR) and holds up to 2 hours, on the same 75 days of 1-minute data, same conservative fill model,
parameters picked on the first 60% and judged on the last 40%.   Run: py research/scalp_wide.py"""
import sys, os, itertools
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scalp_study as S

SIG = dict(k=[2.0, 2.5, 3.0], rsi=[10, 20], off=[0.0, 0.3, 0.6])
EXIT = dict(tgt=[2.0, 3.0, 4.0, 6.0], stop=[2.0, 4.0, 6.0], hold=[30, 60, 120])


def main():
    feats = {s: S.Feat(S.load(s)) for s in S.SYMS if S.load(s)}
    days_is = sum(len(F.r) * 0.6 / 1440 for F in feats.values()); days_oos = sum(len(F.r) * 0.4 / 1440 for F in feats.values())
    for fee_name, fees in S.FEES.items():
        rows = []
        for sp in S.combos(SIG):
            sg = {s: S.signals(F, "mr", dict(sp, wait=3)) for s, F in feats.items()}
            for ep in S.combos(EXIT):
                p = dict(sp, wait=3, **ep)
                ris, ros = [], []
                for s, F in feats.items():
                    k = int(len(F.r) * 0.6)
                    ris += S.simulate(F, [x for x in sg[s] if x[0] < k], p, fees)
                    ros += S.simulate(F, [x for x in sg[s] if x[0] >= k], p, fees)
                rows.append((S.stats(ris, days_is), S.stats(ros, days_oos), p))
        ok = [r for r in rows if r[0]["n"] >= 100]
        pos_is = [r for r in ok if r[0]["avg_bps"] > 0]
        both = [r for r in ok if r[0]["avg_bps"] > 0 and r[1]["avg_bps"] > 0]
        print("\n=== %s: %d settings, %d profitable in-sample, %d profitable in-sample AND out-of-sample ===" % (fee_name, len(ok), len(pos_is), len(both)), flush=True)
        for si, so, p in sorted(ok, key=lambda r: -r[0]["avg_bps"])[:5]:
            print("  IS %+6.2f bps win %2.0f%% %4.1f/day | OOS %+6.2f bps win %2.0f%% %4.1f/day | %s" % (si["avg_bps"], si["win"], si["per_day"], so["avg_bps"], so["win"], so["per_day"], p), flush=True)
        print("  best OOS of all: %+.2f bps" % max(r[1]["avg_bps"] for r in ok))


if __name__ == "__main__":
    main()
